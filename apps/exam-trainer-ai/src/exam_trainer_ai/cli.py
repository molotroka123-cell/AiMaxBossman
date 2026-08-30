import os
import uvicorn

def main():
    port = int(os.getenv("APP_PORT", "8912"))
    uvicorn.run("exam_trainer_ai.api:app", host="127.0.0.1", port=port, reload=False)

if __name__ == "__main__":
    main()
