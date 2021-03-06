#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2019 tribe29 GmbH - License: GNU General Public License v2
# This file is part of Checkmk (https://checkmk.com). It is subject to the terms and
# conditions defined in the file COPYING, which is part of this source code package.

import pytest  # type: ignore[import]

import cmk.utils.paths
import cmk.utils.version as cmk_version
import cmk.gui.watolib.activate_changes as activate_changes
from cmk.gui.watolib.config_sync import ReplicationPath
import testlib


@pytest.fixture(autouse=True)
def restore_orig_replication_paths():
    _orig_paths = activate_changes._replication_paths[:]
    yield
    activate_changes._replication_paths = _orig_paths


def _expected_replication_paths():
    expected = [
        ReplicationPath('dir', 'check_mk',
                        '%s/etc/check_mk/conf.d/wato/' % cmk.utils.paths.omd_root,
                        ['sitespecific.mk']),
        ReplicationPath('dir', 'multisite',
                        '%s/etc/check_mk/multisite.d/wato/' % cmk.utils.paths.omd_root,
                        ['sitespecific.mk']),
        ReplicationPath('file', 'htpasswd', '%s/etc/htpasswd' % cmk.utils.paths.omd_root, []),
        ReplicationPath('file', 'auth.secret', '%s/etc/auth.secret' % cmk.utils.paths.omd_root, []),
        ReplicationPath('file', 'auth.serials', '%s/etc/auth.serials' % cmk.utils.paths.omd_root,
                        []),
        ReplicationPath('dir', 'usersettings', '%s/var/check_mk/web' % cmk.utils.paths.omd_root,
                        ['*/report-thumbnails']),
        ReplicationPath('dir', 'mkps', '%s/var/check_mk/packages' % cmk.utils.paths.omd_root, []),
        ReplicationPath('dir', 'local', '%s/local' % cmk.utils.paths.omd_root, []),
    ]

    if not cmk_version.is_raw_edition():
        expected += [
            ReplicationPath('dir', 'liveproxyd',
                            '%s/etc/check_mk/liveproxyd.d/wato/' % cmk.utils.paths.omd_root,
                            ['sitespecific.mk']),
        ]

    if testlib.is_enterprise_repo():
        expected += [
            ReplicationPath('dir', 'dcd', '%s/etc/check_mk/dcd.d/wato/' % cmk.utils.paths.omd_root,
                            ['sitespecific.mk', 'distributed.mk']),
            ReplicationPath('dir', 'mknotify',
                            '%s/etc/check_mk/mknotifyd.d/wato/' % cmk.utils.paths.omd_root,
                            ['sitespecific.mk']),
        ]

    expected += [
        ReplicationPath('dir', 'mkeventd',
                        '%s/etc/check_mk/mkeventd.d/wato' % cmk.utils.paths.omd_root,
                        ['sitespecific.mk']),
        ReplicationPath('dir', 'mkeventd_mkp',
                        '%s/etc/check_mk/mkeventd.d/mkp/rule_packs' % cmk.utils.paths.omd_root, []),
        ReplicationPath('file', 'diskspace', '%s/etc/diskspace.conf' % cmk.utils.paths.omd_root,
                        []),
    ]

    return expected


@pytest.mark.parametrize("edition_short", ["cre", "cee", "cme"])
def test_get_replication_paths_defaults(edition_short, monkeypatch):
    monkeypatch.setattr(cmk_version, "edition_short", lambda: edition_short)
    expected = _expected_replication_paths()
    assert sorted(activate_changes.get_replication_paths()) == sorted(expected)


@pytest.mark.parametrize("edition_short", ["cre", "cee", "cme"])
@pytest.mark.parametrize("replicate_ec", [None, True, False])
@pytest.mark.parametrize("replicate_mkps", [None, True, False])
def test_get_replication_components(edition_short, monkeypatch, replicate_ec, replicate_mkps):
    monkeypatch.setattr(cmk_version, "edition_short", lambda: edition_short)

    partial_site_config = {}
    if replicate_ec is not None:
        partial_site_config["replicate_ec"] = replicate_ec
    if replicate_mkps is not None:
        partial_site_config["replicate_mkps"] = replicate_mkps

    expected = _expected_replication_paths()

    if not replicate_ec:
        expected = [e for e in expected if e.ident not in ["mkeventd", "mkeventd_mkp"]]

    if not replicate_mkps:
        expected = [e for e in expected if e.ident not in ["local", "mkps"]]

    work_dir = cmk.utils.paths.omd_root
    expected += [
        ReplicationPath(
            ty="file",
            ident="sitespecific",
            path="%s/site_globals/sitespecific.mk" % work_dir,
            excludes=[],
        )
    ]

    assert sorted(activate_changes._get_replication_components(
        work_dir, partial_site_config)) == sorted(expected)


def test_add_replication_paths_pre_17():
    # dir/file, ident, path, optional list of excludes
    activate_changes.add_replication_paths([
        ("dir", "abc", "/path/to/abc"),
        ("dir", "abc", "/path/to/abc", ["e1", "e2"]),
    ])

    assert activate_changes.get_replication_paths()[-2] == ReplicationPath(
        "dir", "abc", "/path/to/abc", [])
    assert activate_changes.get_replication_paths()[-1] == ReplicationPath(
        "dir", "abc", "/path/to/abc", ["e1", "e2"])


def test_add_replication_paths():
    activate_changes.add_replication_paths([
        ReplicationPath("dir", "abc", "/path/to/abc", ["e1", "e2"]),
    ])

    assert activate_changes.get_replication_paths()[-1] == ReplicationPath(
        "dir", "abc", "/path/to/abc", ["e1", "e2"])


@pytest.mark.parametrize("expected, site_status", [
    (False, {}),
    (False, {
        "livestatus_version": "1.8.0"
    }),
    (False, {
        "livestatus_version": "1.8.0"
    }),
    (False, {
        "livestatus_version": "1.7.0p2"
    }),
    (False, {
        "livestatus_version": "1.7.0"
    }),
    (False, {
        "livestatus_version": "1.7.0i1"
    }),
    (False, {
        "livestatus_version": "1.7.0-2020.04.20"
    }),
    (True, {
        "livestatus_version": "1.6.0p2"
    }),
    (True, {
        "livestatus_version": "1.5.0p23"
    }),
])
def test_is_pre_17_remote_site(site_status, expected):
    assert activate_changes._is_pre_17_remote_site(site_status) == expected
