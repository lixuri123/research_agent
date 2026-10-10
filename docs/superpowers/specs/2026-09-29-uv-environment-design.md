# uv 环境管理迁移设计

## 目标

将项目的 Python 依赖与执行流程完整迁移到 uv，以 `pyproject.toml` 作为唯一人工维护的依赖声明，并通过 `uv.lock` 提供可复现安装。

## 范围

- 生成并纳入 `uv.lock`。
- 更新本地开发文档，使用 `uv sync` 和 `uv run`。
- 更新 Docker 构建流程，通过锁文件执行 `uv sync --frozen`。
- 删除 `requirements.txt` 与 `constraints.txt`，避免重复依赖源漂移。
- 保持现有 Python 版本范围 `>=3.10,<3.13` 和运行命令行为不变。

## 依赖模型

运行时依赖继续声明在 `[project].dependencies` 中，开发与测试依赖继续声明在 `[project.optional-dependencies].dev` 中。本地开发使用 `uv sync --extra dev`，生产镜像使用 `uv sync --frozen --no-dev`。

## 本地工作流

uv 自动创建和维护 `.venv`。常用命令统一为：

- 安装开发环境：`uv sync --extra dev`
- 启动 API：`uv run uvicorn research_agent.api:app --host 127.0.0.1 --port 8000`
- 启动 Worker：`uv run python -m research_agent.worker`
- 运行测试：`uv run pytest tests -q`
- 运行演示和回归脚本：`uv run python ...`

## 容器构建

Docker 镜像安装 uv，先复制 `pyproject.toml` 与 `uv.lock` 以利用依赖层缓存，再以冻结模式同步生产依赖。应用通过项目虚拟环境内的命令启动，确保容器和本地使用同一锁定依赖图。

## 错误处理与验证

- `uv lock` 必须成功解析项目支持的 Python 版本范围。
- `uv sync --extra dev --frozen` 必须能按锁文件创建环境。
- 完整测试套件必须通过。
- Dockerfile 至少完成静态检查；若本机 Docker 可用，再执行镜像构建验证。
- README 中不得残留 `pip install`、手工 `venv` 或直接调用 `.venv/Scripts/python.exe` 的主流程说明。

## 非目标

- 不调整应用功能或依赖版本约束，除非 uv 的解析结果证明现有约束不可满足。
- 不改变 Docker Compose 服务结构或运行时配置。
- 不引入其他环境管理器或额外依赖清单。
