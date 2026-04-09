"""项目命令行入口模块。

该文件仅用于本地快速自检，不负责启动 Webhook 服务。
正式运行请使用 `qq_agent.qq_bot:app` 配合 uvicorn。
"""

def main() -> None:
    """输出项目就绪提示，用于本地冒烟测试。"""
    print("QQ agent project is ready.")


if __name__ == "__main__":
    main()
