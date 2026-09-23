"""配置框架：集中管理 API Key、模型、路径等。
DeepSeek 使用 OpenAI 兼容接口，用 openai SDK 调用。
"""
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 轻量 .env 加载（优先环境变量，其次 .env 文件）
def _load_env(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _project_root() -> Path:
    """定位项目根目录，兼容 PyInstaller 打包环境。"""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        _load_env(base / ".env")                      # 打包内的 .env
        _load_env(Path(sys.executable).parent / ".env")  # exe 同目录
        return base
    return Path(__file__).resolve().parent.parent


_load_env(_project_root() / ".env")

# 项目根目录
BASE_DIR = _project_root()
OUTPUT_DIR = BASE_DIR / "outputs"
try:
    OUTPUT_DIR.mkdir(exist_ok=True)
except OSError:
    import tempfile
    OUTPUT_DIR = Path(tempfile.gettempdir()) / "ai_research_outputs"
    OUTPUT_DIR.mkdir(exist_ok=True)

# 支持的澄清维度清单（供 Clarifier 动态挑选）
CLARIFY_DIMENSIONS = [
    "audience",      # 目标受众
    "purpose",       # 用途/决策
    "time_range",    # 时间范围
    "market",        # 地理/市场范围
    "depth",         # 深度
    "angle",         # 立场/视角
    "must_include",  # 必含项
    "must_exclude",  # 排除项
    "format",        # 格式偏好
]


@dataclass
class Settings:
    # ---- LLM（DeepSeek）----
    deepseek_api_key: str = field(
        default_factory=lambda: os.getenv("DEEPSEEK_API_KEY", "")
    )
    # DeepSeek OpenAI 兼容端点
    base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    # 性价比优先：deepseek-chat(V3) 比 deepseek-reasoner 便宜 10 倍以上
    model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

    # ---- 检索 ----
    # 当前 MVP 使用内置联网检索能力；独立部署可切 Tavily/SerpAPI
    search_provider: str = os.getenv("SEARCH_PROVIDER", "tavily")  # builtin | tavily
    tavily_api_key: str = field(
        default_factory=lambda: os.getenv("TAVILY_API_KEY", "")
    )

    # ---- 输出 ----
    default_format: str = os.getenv("OUTPUT_FORMAT", "pdf")  # pdf | md | both
    output_dir: Path = OUTPUT_DIR

    def is_llm_ready(self) -> bool:
        return bool(self.deepseek_api_key)

    def is_search_ready(self) -> bool:
        return True  # builtin 搜索无需 Key


# 全局设置单例
settings = Settings()
