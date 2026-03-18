import uvicorn
from backend.config import get_config


def main():
    config = get_config()
    uvicorn.run(
        "backend.app:app",
        host=config["server"]["host"],
        port=config["server"]["port"],
        reload=True,
    )


if __name__ == "__main__":
    main()
