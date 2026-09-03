import os
import uvicorn


def main():
    port = int(os.getenv("APP_PORT", "8920"))
    uvicorn.run("osiris.api:app", host="127.0.0.1", port=port, reload=False)


if __name__ == "__main__":
    main()
