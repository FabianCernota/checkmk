# Stubs for kubernetes.client.models.v1_resource_requirements (Python 2)
#
# NOTE: This dynamically typed stub was automatically generated by stubgen.

from typing import Any, Optional

class V1ResourceRequirements:
    swagger_types: Any = ...
    attribute_map: Any = ...
    discriminator: Any = ...
    limits: Any = ...
    requests: Any = ...
    def __init__(self, limits: Optional[Any] = ..., requests: Optional[Any] = ...) -> None: ...
    @property
    def limits(self): ...
    @limits.setter
    def limits(self, limits: Any) -> None: ...
    @property
    def requests(self): ...
    @requests.setter
    def requests(self, requests: Any) -> None: ...
    def to_dict(self): ...
    def to_str(self): ...
    def __eq__(self, other: Any): ...
    def __ne__(self, other: Any): ...