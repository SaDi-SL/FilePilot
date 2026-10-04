from dataclasses import dataclass


@dataclass(frozen=True)
class ProductIdentity:
    product_name: str
    version: str
    channel: str
    release_basis: str

    @property
    def display_version(self) -> str:
        return self.version

    @property
    def build_description(self) -> str:
        return f"Development build based on {self.release_basis}"


# V1.1.0 is the latest release ancestor. This tree contains unreleased work
# after that tag, so it must not identify itself as the 1.1.0 release build.
PRODUCT_IDENTITY = ProductIdentity(
    product_name="FilePilot",
    version="1.1.0+development",
    channel="development",
    release_basis="V1.1.0",
)
