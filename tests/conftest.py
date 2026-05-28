import os
import tempfile


_ISOLATED_HOME = tempfile.mkdtemp(prefix="catown-root-tests-")
_STATE_DIR = os.path.join(_ISOLATED_HOME, "state")
_PROJECTS_ROOT = os.path.join(_ISOLATED_HOME, "projects")
_WORKSPACES_DIR = os.path.join(_ISOLATED_HOME, "workspaces")

os.environ["CATOWN_HOME"] = _ISOLATED_HOME
os.environ["CATOWN_STATE_DIR"] = _STATE_DIR
os.environ["CATOWN_PROJECTS_ROOT"] = _PROJECTS_ROOT
os.environ["CATOWN_WORKSPACES_DIR"] = _WORKSPACES_DIR
os.environ["DATABASE_URL"] = os.path.join(_STATE_DIR, "test.db")
os.environ["LOG_LEVEL"] = "WARNING"
os.environ.setdefault("LLM_API_KEY", "test-key")
os.environ.setdefault("LLM_BASE_URL", "http://localhost:9999/v1")
os.environ.setdefault("LLM_MODEL", "test-model")

for _env_name in (
    "AGENT_CONFIG_FILE",
    "PIPELINE_CONFIG_FILE",
    "SKILLS_CONFIG_FILE",
    "SKILL_MARKETPLACES_CONFIG_FILE",
    "CATOWN_CONFIG_DIR",
):
    os.environ.pop(_env_name, None)
