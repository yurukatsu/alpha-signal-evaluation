from dataclasses import dataclass, field

from ..config import _env_factory
from .irddb import IRDDBConfig


@dataclass(kw_only=True)
class RiskModelsConfig(IRDDBConfig):
    db: str = "RISK_MODELS"
    username: str = field(default_factory=_env_factory("RISK_MODELS_USERNAME"))
    password: str = field(default_factory=_env_factory("RISK_MODELS_PASSWORD"), repr=False)
