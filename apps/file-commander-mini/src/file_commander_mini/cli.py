import os
import uvicorn

def main():
    port = int(os.getenv("APP_PORT", "8911"))
    uvicorn.run("file_commander_mini.api:app", host="127.0.0.1", port=port, reload=False)

if __name__ == "__main__":
    main()
