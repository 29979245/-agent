"""ChemAI 后端包。

包级副作用：最先加载项目根 .env。原因：app.core.security 在模块 import 阶段
读取 JWT_SECRET（见其顶部 os.environ.get），必须保证任何 app.* 子模块
import 之前 .env 已注入 os.environ。
"""
from app.core.env import load_dotenv_file

load_dotenv_file()
