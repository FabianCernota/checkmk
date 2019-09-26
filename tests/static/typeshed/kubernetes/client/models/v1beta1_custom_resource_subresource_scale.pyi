# Stubs for kubernetes.client.models.v1beta1_custom_resource_subresource_scale (Python 2)
#
# NOTE: This dynamically typed stub was automatically generated by stubgen.

from typing import Any, Optional

class V1beta1CustomResourceSubresourceScale:
    swagger_types: Any = ...
    attribute_map: Any = ...
    discriminator: Any = ...
    label_selector_path: Any = ...
    spec_replicas_path: Any = ...
    status_replicas_path: Any = ...
    def __init__(self, label_selector_path: Optional[Any] = ..., spec_replicas_path: Optional[Any] = ..., status_replicas_path: Optional[Any] = ...) -> None: ...
    @property
    def label_selector_path(self): ...
    @label_selector_path.setter
    def label_selector_path(self, label_selector_path: Any) -> None: ...
    @property
    def spec_replicas_path(self): ...
    @spec_replicas_path.setter
    def spec_replicas_path(self, spec_replicas_path: Any) -> None: ...
    @property
    def status_replicas_path(self): ...
    @status_replicas_path.setter
    def status_replicas_path(self, status_replicas_path: Any) -> None: ...
    def to_dict(self): ...
    def to_str(self): ...
    def __eq__(self, other: Any): ...
    def __ne__(self, other: Any): ...