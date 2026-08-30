import os
import uvicorn

def main():
    port = int(os.getenv("APP_PORT", "8910"))
    uvicorn.run("bossman_accountant.api:app", host="127.0.0.1", port=port, reload=False)

if __name__ == "__main__":
    main()
