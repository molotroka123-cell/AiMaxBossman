-- Отдельная БД для агентов Bossman и RAG (LiteLLM живёт в БД litellm)
CREATE DATABASE bossman;
\connect bossman
CREATE EXTENSION IF NOT EXISTS vector;
