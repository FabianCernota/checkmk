#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2019 tribe29 GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

import abc
import sys
from typing import Any, Callable, cast, Union, Tuple, Dict, Set, List, Optional, Generic  # pylint: disable=unused-import
import six

import cmk.utils.debug

import cmk.base.config as config
import cmk.base.caching as caching
import cmk.base.ip_lookup as ip_lookup
import cmk.base.item_state as item_state
import cmk.base.check_utils
from cmk.base.api import PluginName
from cmk.base.api.agent_based.utils import parse_string_table
from cmk.base.api.agent_based.section_types import AgentParseFunction, AgentSectionPlugin, SNMPParseFunction, SNMPSectionPlugin  # pylint: disable=unused-import
from cmk.utils.type_defs import HostName, HostAddress, ServiceName  # pylint: disable=unused-import
from cmk.base.check_utils import (  # pylint: disable=unused-import
    CheckPluginName, SectionCacheInfo, PiggybackRawData, AbstractSectionContent, SectionName,
    ParsedSectionContent, BoundedAbstractRawData, BoundedAbstractSections,
    BoundedAbstractPersistedSections, BoundedAbstractSectionContent, FinalSectionContent,
)

from cmk.base.exceptions import MKParseFunctionError

MultiHostSectionsData = Dict[Tuple[HostName, Optional[HostAddress]], "AbstractHostSections"]


class AbstractHostSections(
        six.with_metaclass(
            abc.ABCMeta, Generic[BoundedAbstractRawData, BoundedAbstractSections,
                                 BoundedAbstractPersistedSections, BoundedAbstractSectionContent])):
    """A wrapper class for the host information read by the data sources

    It contains the following information:

        1. sections:                A dictionary from section_name to a list of rows,
                                    the section content
        2. piggybacked_raw_data:    piggy-backed data for other hosts
        3. persisted_sections:      Sections to be persisted for later usage
        4. cache_info:              Agent cache information
                                    (dict section name -> (cached_at, cache_interval))
    """
    def __init__(self, sections, cache_info, piggybacked_raw_data, persisted_sections):
        # type: (BoundedAbstractSections, SectionCacheInfo, PiggybackRawData, BoundedAbstractPersistedSections) -> None
        super(AbstractHostSections, self).__init__()
        self.sections = sections
        self.cache_info = cache_info
        self.piggybacked_raw_data = piggybacked_raw_data
        self.persisted_sections = persisted_sections

    # TODO: It should be supported that different sources produce equal sections.
    # this is handled for the self.sections data by simply concatenating the lines
    # of the sections, but for the self.cache_info this is not done. Why?
    # TODO: checking.execute_check() is using the oldest cached_at and the largest interval.
    #       Would this be correct here?
    def update(self, host_sections):
        # type: (AbstractHostSections) -> None
        """Update this host info object with the contents of another one"""
        for section_name, section_content in host_sections.sections.items():
            self._extend_section(section_name, section_content)

        for hostname, raw_lines in host_sections.piggybacked_raw_data.items():
            self.piggybacked_raw_data.setdefault(hostname, []).extend(raw_lines)

        if host_sections.cache_info:
            self.cache_info.update(host_sections.cache_info)

        if host_sections.persisted_sections:
            self.persisted_sections.update(host_sections.persisted_sections)

    @abc.abstractmethod
    def _extend_section(self, section_name, section_content):
        # type: (SectionName, BoundedAbstractSectionContent) -> None
        raise NotImplementedError()

    def add_cached_section(self, section_name, section, persisted_from, persisted_until):
        # type: (SectionName, BoundedAbstractSectionContent , int, int) -> None
        self.cache_info[section_name] = (persisted_from, persisted_until - persisted_from)
        # TODO: Find out why mypy complains about this
        self.sections[section_name] = section  # type: ignore[assignment]


class MultiHostSections(object):  # pylint: disable=useless-object-inheritance
    """Container object for wrapping the host sections of a host being processed
    or multiple hosts when a cluster is processed. Also holds the functionality for
    merging these information together for a check"""
    def __init__(self):
        # type: () -> None
        super(MultiHostSections, self).__init__()
        self._config_cache = config.get_config_cache()
        self._multi_host_sections = {}  # type: MultiHostSectionsData
        self._section_content_cache = caching.DictCache()
        # This is not quite the same as section_content_cache.
        # It is introduced for the changed data handling with the migration
        # to 'agent_based' plugins.
        # It holy holds the result of individual calls of the parse_function.
        self._parsed_sections = caching.DictCache()
        self._parsed_to_raw_map = caching.DictCache()

    def add_or_get_host_sections(self, hostname, ipaddress, deflt):
        # type: (HostName, Optional[HostAddress], AbstractHostSections) -> AbstractHostSections
        return self._multi_host_sections.setdefault((hostname, ipaddress), deflt)

    def get_host_sections(self):
        # type: () -> MultiHostSectionsData
        return self._multi_host_sections

    def get_section_kwargs(self, host_name, ip_address, subscribed_sections):
        # type: (HostName, Optional[HostAddress], List[PluginName]) -> Dict[str, Any]
        """Prepares section keyword arguments for a non-cluster host

        It returns a dictionary containing one entry (may be None) for each
        of the required sections, or an empty dictionary of no data was found at all.
        """
        kwarg_keys = ["section"] if len(subscribed_sections) == 1 else [
            "section_%s" % s for s in subscribed_sections
        ]

        kwargs = {
            kwarg_key: self.get_parsed_section(host_name, ip_address, sec_name)
            for kwarg_key, sec_name in zip(kwarg_keys, subscribed_sections)
        }

        return {} if all(v is None for v in kwargs.values()) else kwargs

    def get_section_cluster_kwargs(self, host_name, subscribed_sections, service_description):
        """Prepares section keyword arguments for a cluster host

        It returns a dictionary containing one optional dictionary[Host, ParsedSection]
        for each of the required sections, or an empty dictionary of no data was found at all.
        """
        # see which host entries we must use
        nodes_of_clustered_service = self._get_nodes_of_clustered_service(
            host_name, service_description) or []
        host_entries = self._get_host_entries(host_name, None)
        used_entries = [he for he in host_entries if he[0] in nodes_of_clustered_service]

        kwargs = {}  # type: Dict[str, Dict[str, Any]]
        for node_name, node_ip in used_entries:
            node_kwargs = self.get_section_kwargs(node_name, node_ip, subscribed_sections)
            for key, sections_node_data in node_kwargs.items():
                kwargs.setdefault(key, {})[node_name] = sections_node_data

        # return empty dict if no data is found
        return {} if all(all(v is None for v in s.values()) for s in kwargs.values()) else kwargs

    def get_cache_info(self, section_names):
        # type: (List[PluginName]) -> Union[Tuple[None, None], Tuple[int, int]]
        """Aggregate information about the age of the data in the agent sections
        """
        cached_ats = []  # type: List[int]
        intervals = []  # type: List[int]
        for (host_name, ip_address), host_sections in self._multi_host_sections.items():
            raw_sections = [
                self.get_raw_section(host_name, ip_address, parsed_section_name)
                for parsed_section_name in section_names
            ]
            raw_section_names = (str(s.name) for s in raw_sections if s is not None)
            for name in raw_section_names:
                cache_info = host_sections.cache_info.get(name)
                if cache_info and cache_info != (None, None):
                    cached_ats.append(cache_info[0])
                    intervals.append(cache_info[1])

        return (min(cached_ats), max(intervals)) if cached_ats else (None, None)

    def get_parsed_section(
        self,
        host_name,  # type: HostName
        ip_address,  # type: Optional[HostAddress]
        section_name,  # type: PluginName
    ):
        # type: (...) -> Optional[ParsedSectionContent]
        cache_key = (host_name, ip_address, section_name)
        if cache_key in self._parsed_sections:
            return self._parsed_sections[cache_key]

        section_def = self.get_raw_section(host_name, ip_address, section_name)
        if section_def is None:
            # no section creating the desired one was found, assume a 'default' section:
            raw_section_name = section_name
            parse_function = parse_string_table  # type: Union[SNMPParseFunction, AgentParseFunction]
        else:
            raw_section_name = section_def.name
            parse_function = section_def.parse_function

        try:
            hosts_raw_sections = self._multi_host_sections[(host_name, ip_address)].sections
            string_table = hosts_raw_sections[str(raw_section_name)]
        except KeyError:
            return self._parsed_sections.setdefault(cache_key, None)

        try:
            parsed = parse_function(string_table)
        except Exception:
            if cmk.utils.debug.enabled():
                raise
            raise MKParseFunctionError(*sys.exc_info())

        return self._parsed_sections.setdefault(cache_key, parsed)

    def get_raw_section(self, host_name, ip_address, section_name):
        # type: (HostName, Optional[HostAddress], PluginName) -> Union[AgentSectionPlugin, SNMPSectionPlugin]
        """Get the raw sections name that will be parsed into the required section

        Raw sections may get renamed once they are parsed, if they declare it. This function
        deals with the task of determining which section we need to parse, in order to end
        up with the desired parsed section.
        This depends on the available raw sections, and thus on the host.
        """
        cache_key = (host_name, ip_address, section_name)
        if cache_key in self._parsed_to_raw_map:
            return self._parsed_to_raw_map[cache_key]

        try:
            hosts_raw_sections = self._multi_host_sections[(host_name, ip_address)].sections
        except KeyError:
            return self._parsed_to_raw_map.setdefault(cache_key, None)

        available_raw_sections = [PluginName(n) for n in hosts_raw_sections]
        section_def = config.get_parsed_section_creator(section_name, available_raw_sections)
        return self._parsed_to_raw_map.setdefault(cache_key, section_def)

    def get_section_content(self,
                            hostname,
                            ipaddress,
                            check_plugin_name,
                            for_discovery,
                            service_description=None):
        # type: (HostName, Optional[HostAddress], CheckPluginName, bool, Optional[ServiceName]) -> FinalSectionContent
        """Prepares the section_content construct for a Check_MK check on ANY host

        The section_content construct is then handed over to the check, inventory or
        discovery functions for doing their work.

        If the host is a cluster, the sections from all its nodes is merged together
        here. Optionally the node info is added to the nodes section content.

        It handles the whole data and cares about these aspects:

        a) Extract the section_content for the given check_plugin_name
        b) Adds node_info to the section_content (if check asks for this)
        c) Applies the parse function (if check has some)
        d) Adds extra_sections (if check asks for this)
           and also applies node_info and extra_section handling to this

        It can return an section_content construct or None when there is no section content
        for this check available.
        """

        section_name = cmk.base.check_utils.section_name_of(check_plugin_name)
        nodes_of_clustered_service = self._get_nodes_of_clustered_service(
            hostname, service_description)
        cache_key = (hostname, ipaddress, section_name, for_discovery,
                     bool(nodes_of_clustered_service))

        try:
            return self._section_content_cache[cache_key]
        except KeyError:
            section_content = self._get_section_content(hostname, ipaddress, check_plugin_name,
                                                        section_name, for_discovery,
                                                        nodes_of_clustered_service)
            self._section_content_cache[cache_key] = section_content
            return section_content

    def _get_nodes_of_clustered_service(self, hostname, service_description):
        # type: (HostName, Optional[ServiceName]) -> Optional[List[HostName]]
        """Returns the node names if a service is clustered, otherwise 'None' in order to
        decide whether we collect section content of the host or the nodes.

        For real hosts or nodes for which the service is not clustered we return 'None',
        thus the caching works as before.

        If a service is assigned to a cluster we receive the real nodename. In this
        case we have to sort out data from the nodes for which the same named service
        is not clustered (Clustered service for overlapping clusters).

        We also use the result for the section cache.
        """
        if not service_description:
            return None

        host_config = self._config_cache.get_host_config(hostname)
        nodes = host_config.nodes

        if nodes is None:
            return None

        return [
            nodename for nodename in nodes
            if hostname == self._config_cache.host_of_clustered_service(
                nodename, service_description)
        ]

    def _get_section_content(self, hostname, ipaddress, check_plugin_name, section_name,
                             for_discovery, nodes_of_clustered_service):
        # type: (HostName, Optional[HostAddress], CheckPluginName, SectionName, bool, Optional[List[HostName]]) -> Union[None, ParsedSectionContent, List[ParsedSectionContent]]

        # First abstract cluster / non cluster hosts
        host_entries = self._get_host_entries(hostname, ipaddress)

        # Now get the section_content from the required hosts and merge them together to
        # a single section_content. For each host optionally add the node info.
        section_content = None  # type: Optional[AbstractSectionContent]
        for host_entry in host_entries:
            if nodes_of_clustered_service and host_entry[0] not in nodes_of_clustered_service:
                continue

            try:
                host_section_content = self._multi_host_sections[host_entry].sections[section_name]
            except KeyError:
                continue

            host_section_content = self._update_with_node_column(host_section_content,
                                                                 check_plugin_name, host_entry[0],
                                                                 for_discovery)

            if section_content is None:
                section_content = host_section_content[:]
            else:
                section_content += host_section_content

        if section_content is None:
            return None

        assert isinstance(section_content, list)

        section_content = self._update_with_parse_function(section_content, section_name)
        section_contents = self._update_with_extra_sections(section_content, hostname, ipaddress,
                                                            section_name, for_discovery)
        return section_contents

    def _get_host_entries(self, hostname, ipaddress):
        # type: (HostName, Optional[HostAddress]) -> List[Tuple[HostName, Optional[HostAddress]]]
        host_config = self._config_cache.get_host_config(hostname)
        if host_config.nodes is None:
            return [(hostname, ipaddress)]

        return [(node_hostname, ip_lookup.lookup_ip_address(node_hostname))
                for node_hostname in host_config.nodes]

    def _update_with_node_column(self, section_content, check_plugin_name, hostname, for_discovery):
        # type: (AbstractSectionContent, CheckPluginName, HostName, bool) -> AbstractSectionContent
        """Add cluster node information to the section content

        If the check want's the node column, we add an additional column (as the first column) with the
        name of the node or None in case of non-clustered nodes.

        Whether or not a node info is requested by a check is not a property of the agent section. Each
        check/subcheck can define the requirement on it's own.

        When called for the discovery, the node name is always set to "None". During the discovery of
        services we behave like a non-cluster because we don't know whether or not the service will
        be added to the cluster or the node. This decision is made later during creation of the
        configuation. This means that the discovery function must work independent from the node info.
        """
        if check_plugin_name not in config.check_info or not config.check_info[check_plugin_name][
                "node_info"]:
            return section_content  # unknown check_plugin_name or does not want node info -> do nothing

        node_name = None  # type: Optional[HostName]
        if not for_discovery and self._config_cache.clusters_of(hostname):
            node_name = hostname

        return self._add_node_column(section_content, node_name)

    # TODO: Add correct type hint for node wrapped SectionContent. We would have to create some kind
    # of AbstractSectionContentWithNodeInfo.
    def _add_node_column(self, section_content, nodename):
        # type: (AbstractSectionContent, Optional[HostName]) -> AbstractSectionContent
        new_section_content = []
        node_text = six.text_type(nodename) if isinstance(nodename, str) else nodename
        for line in section_content:
            if len(line) > 0 and isinstance(line[0], list):
                new_entry = []
                for entry in line:
                    new_entry.append([node_text] + entry)  # type: ignore[operator]
                new_section_content.append(new_entry)
            else:
                new_section_content.append([node_text] + line)  # type: ignore[arg-type,operator]
        return new_section_content  # type: ignore[return-value]

    def _update_with_extra_sections(self, section_content, hostname, ipaddress, section_name,
                                    for_discovery):
        # type: (ParsedSectionContent, HostName, Optional[HostAddress], SectionName, bool) -> Union[ParsedSectionContent, List[ParsedSectionContent]]
        """Adds additional agent sections to the section_content the check receives.

        Please note that this is not a check/subcheck individual setting. This option is related
        to the agent section.
        """
        if section_name not in config.check_info or not config.check_info[section_name][
                "extra_sections"]:
            return section_content

        # In case of extra_sections the existing info is wrapped into a new list to which all
        # extra sections are appended
        section_contents = [section_content]
        for extra_section_name in config.check_info[section_name]["extra_sections"]:
            section_contents.append(
                self.get_section_content(hostname, ipaddress, extra_section_name, for_discovery))

        return section_contents

    def _update_with_parse_function(self, section_content, section_name):
        # type: (AbstractSectionContent, SectionName) -> ParsedSectionContent
        """Transform the section_content using the defined parse functions.

        Some checks define a parse function that is used to transform the section_content
        somehow. It is applied by this function.

        Please note that this is not a check/subcheck individual setting. This option is related
        to the agent section.

        All exceptions raised by the parse function will be catched and re-raised as
        MKParseFunctionError() exceptions."""

        # TODO (mo): change this function to expect a PluginName as argument
        section_plugin_name = PluginName(section_name)
        section_plugin = config.get_registered_section_plugin(section_plugin_name)
        if section_plugin is None:
            # use legacy parse function for unmigrated sections
            parse_function = config.check_info.get(section_name, {}).get("parse_function")
            if parse_function is None:
                return section_content
        else:
            # TODO (mo): deal with the parsed_section_name feature (CMK-4006)
            if section_plugin.name != section_plugin.parsed_section_name:
                raise NotImplementedError()
            parse_function = section_plugin.parse_function

        # TODO (mo): make this unnecessary
        parse_function = cast(Callable[[AbstractSectionContent], ParsedSectionContent],
                              parse_function)

        # TODO: Item state needs to be handled in local objects instead of the
        # item_state._cached_item_states object
        # TODO (mo): ValueStores (formally Item state) need to be *only* available
        # from within the check function, nowhere else.
        orig_item_state_prefix = item_state.get_item_state_prefix()
        try:
            item_state.set_item_state_prefix(section_name, None)
            return parse_function(section_content)
        except Exception:
            if cmk.utils.debug.enabled():
                raise
            raise MKParseFunctionError(*sys.exc_info())
        finally:
            item_state.set_item_state_prefix(*orig_item_state_prefix)

    def get_check_plugin_names(self):
        # type: () -> Set[CheckPluginName]
        # TODO: There is a function 'section_name_of' in check_utils.py
        # but no inverse function, ie. get all subchecks of main check.
        check_keys = set(config.check_info)
        check_plugin_names = set()
        for v in self._multi_host_sections.values():
            for k in v.sections:
                for check_k in check_keys:
                    if check_k.startswith(k):
                        check_plugin_names.add(check_k)
        return check_plugin_names
