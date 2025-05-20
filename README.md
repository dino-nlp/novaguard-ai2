# NovaGuard-AI: Intelligent Code Analysis Platform

- **Version:** 2.0 (MVP1 In Progress)
- **Last Updated:** May 14, 2025

NovaGuard-AI is a web-based platform designed to provide intelligent and in-depth automated code analysis. It integrates with GitHub to analyze pull requests, leveraging Large Language Models (LLMs) via Ollama to offer insights beyond traditional static analysis tools. The goal is to help development teams improve code quality, identify potential issues early, and enhance overall development efficiency.

## Table of Contents

- [NovaGuard-AI: Intelligent Code Analysis Platform](#novaguard-ai-intelligent-code-analysis-platform)
  - [Table of Contents](#table-of-contents)
  - [Core Features (MVP1)](#core-features-mvp1)
  - [Technology Stack](#technology-stack)
  - [Project Structure](#project-structure)
  - [Prerequisites](#prerequisites)
  - [Setup and Installation](#setup-and-installation)
    - [1. Clone the Repository](#1-clone-the-repository)
    - [2. Environment Variables](#2-environment-variables)
    - [3. Build and Run Docker Services](#3-build-and-run-docker-services)
    - [4. Initialize Ollama Model](#4-initialize-ollama-model)
    - [5. Initialize Database Schema](#5-initialize-database-schema)
    - [6. (Optional) Reset Database](#6-optional-reset-database)
  - [Running the Application](#running-the-application)
  - [Accessing the Application](#accessing-the-application)
  - [Usage Guide](#usage-guide)
    - [1. Register and Login](#1-register-and-login)
    - [2. Connect GitHub Account](#2-connect-github-account)
    - [3. Add a Project](#3-add-a-project)
    - [4. Automatic PR Analysis](#4-automatic-pr-analysis)
    - [5. View Analysis Reports](#5-view-analysis-reports)
    - [6. Delete a Project](#6-delete-a-project)
  - [Environment Variables Details](#environment-variables-details)
  - [Development](#development)
    - [Running Tests](#running-tests)
    - [Prompts](#prompts)
  - [Future Roadmap (Post-MVP1 Highlights)](#future-roadmap-post-mvp1-highlights)
  - [Contributing](#contributing)
  - [License](#license)

## Core Features (MVP1)

* **User Authentication:** Secure user registration and login (email/password & GitHub OAuth).
* **GitHub Integration:** Connect your GitHub account to list and add repositories.
* **Project Management:** Add, view, and manage projects for analysis.
* **Automated Pull Request Analysis:**
    * Triggered by GitHub webhooks for new/updated PRs.
    * Fetches PR details, diffs, and changed file content.
    * Uses an LLM (via Ollama and Langchain) to analyze code for potential issues (logic errors, basic vulnerabilities).
    * Stores analysis findings, including relevant code snippets.
* **Web-Based Reporting:** View detailed analysis reports for each PR.
* **Project Deletion:** Ability to remove projects and attempt to clean up associated GitHub webhooks.

## Technology Stack

* **Backend:** Python, FastAPI
* **LLM Orchestration:** Langchain
* **LLM Runtime:** Ollama (e.g., `codellama:7b-instruct-q4_K_M`)
* **Database:** PostgreSQL
* **Message Queue:** Apache Kafka
* **ORM:** SQLAlchemy
* **Frontend (MVP):** Server-side rendered HTML with Jinja2 templates, Tailwind CSS.
* **Containerization:** Docker, Docker Compose

## Project Structure

The project is primarily composed of the `novaguard-backend` application:

```

novaguard-ai2/
├── novaguard-backend/
│   ├── app/
│   │   ├── analysis\_module/    \# Logic for findings
│   │   ├── analysis\_worker/    \# Kafka consumer, LLM analysis logic
│   │   │   └── llm\_schemas.py  \# Pydantic models for LLM output
│   │   ├── auth\_service/       \# Authentication (API, CRUD, schemas)
│   │   ├── common/             \# Shared utilities (GitHub client, Kafka producer)
│   │   ├── core/               \# Core config, DB, security
│   │   ├── llm\_service/        \# (Currently bypassed by direct Langchain use)
│   │   ├── models/             \# SQLAlchemy ORM models
│   │   ├── project\_service/    \# Project management (API, CRUD, schemas)
│   │   ├── prompts/            \# LLM Prompt templates (e.g., .md files)
│   │   ├── static/             \# Static files (CSS, JS)
│   │   ├── templates/          \# Jinja2 HTML templates
│   │   ├── webhook\_service/    \# GitHub webhook handling (API, CRUD, schemas)
│   │   └── main.py             \# FastAPI app entrypoint, UI routes
│   ├── database/
│   │   └── schema.sql          \# PostgreSQL schema definition
│   ├── tests/                  \# Unit and integration tests
│   ├── .env.example            \# Example environment file
│   ├── Dockerfile
│   └── requirements.txt
├── scripts/                    \# Utility scripts (DB init, reset)
├── docker compose.yml
└── README.md                   \# This file

````

## Prerequisites

* Docker and Docker Compose installed.
* Git installed.
* A GitHub account (and potentially a repository to test with).
* An Ollama-compatible LLM pulled (e.g., `codellama:7b-instruct-q4_K_M`).

## Setup and Installation

### 1. Clone the Repository

```bash
git clone https://github.com/dino-nlp/novaguard-ai2.git
cd novaguard-ai2
````

### 2\. Environment Variables

Copy the example environment file and customize it:

```bash
cp novaguard-backend/.env.example novaguard-backend/.env
```

Edit `novaguard-backend/.env` with your specific configurations. See the [Environment Variables Details](https://www.google.com/search?q=%23environment-variables-details) section below for more information on each variable.
**Crucial variables to set:**

  * `SECRET_KEY` (for JWT)
  * `FERNET_ENCRYPTION_KEY` (for encrypting GitHub tokens)
  * `SESSION_SECRET_KEY` (for UI sessions)
  * `GITHUB_CLIENT_ID` (for GitHub OAuth)
  * `GITHUB_CLIENT_SECRET` (for GitHub OAuth)
  * `GITHUB_WEBHOOK_SECRET` (for verifying GitHub webhooks)
  * `NOVAGUARD_PUBLIC_URL` (Publicly accessible URL for GitHub webhooks, e.g., ngrok URL during development)

### 3\. Build and Run Docker Services

This command will build the necessary images (if not already built) and start all services defined in `docker compose.yml` (PostgreSQL, Kafka, Zookeeper, Ollama, and the NovaGuard backend API & Worker).

```bash
docker compose up -d --build
```

To check the status of running containers:

```bash
docker compose ps
```

To view logs for a specific service (e.g., the API or worker):

```bash
docker compose logs -f novaguard_backend_api
docker compose logs -f novaguard_analysis_worker
docker compose logs -f ollama
```

### 4\. Initialize Ollama Model

If you haven't pulled the LLM model for Ollama yet, or want to ensure it's available inside the Ollama container:

```bash
docker compose exec ollama ollama pull codellama:7b-instruct-q4_K_M
docker compose exec ollama ollama pull qwen2.5-coder:7b-instruct
```

(Replace `codellama:7b-instruct-q4_K_M` with your desired default model if different, and ensure `OLLAMA_DEFAULT_MODEL` in `.env` matches.)

### 5\. Initialize Database Schema

This script will apply the SQL schema to your PostgreSQL database.

```bash
./scripts/init_db.sh
```

Alternatively, you can run the command manually (ensure services are up):

```bash
cat novaguard-backend/database/schema.sql | docker compose exec -T postgres_db psql -U novaguard_user -d novaguard_db
```

### 6\. (Optional) Reset Database

If you need to wipe all data and re-apply the schema (e.g., after schema changes):

```bash
./scripts/reset_db.sh
```

**Warning:** This will delete all data in your NovaGuard database.

## Running the Application

If all services are not yet running from the setup step:

```bash
docker compose up -d
```

The backend API will typically be available at `http://localhost:8000`.

## Accessing the Application

  * **Web UI:** `http://localhost:8000/`
  * **API Documentation (Swagger UI):** `http://localhost:8000/docs`
  * **API Documentation (ReDoc):** `http://localhost:8000/redoc`

## Usage Guide

### 1\. Register and Login

  * Navigate to `http://localhost:8000/`.
  * Click "Register" to create a new account using email and password.
  * Alternatively, click "Login" and then "Sign in with GitHub" for OAuth-based login/registration.

### 2\. Connect GitHub Account

  * After logging in, go to the Dashboard (`http://localhost:8000/dashboard`).
  * If your GitHub account is not yet connected, click "Connect GitHub Account". This will redirect you to GitHub for authorization.
  * Authorize NovaGuard-AI to access your repositories (permissions requested: `repo`, `read:user`, `user:email`).

### 3\. Add a Project

  * On the Dashboard, you will see a list of your available GitHub repositories (if your GitHub account is connected).
  * Click "Add to NovaGuard" next to a repository. This will prefill the project details.
  * Alternatively, click "Add New Project Manually" and fill in:
      * **Repository Name:** Full name (e.g., `owner/repo-name`).
      * **GitHub Repository ID:** The numerical ID from GitHub (can be found via GitHub API or sometimes in the repo URL).
      * **Main Branch:** The primary branch to analyze (e.g., `main`, `master`).
      * (Optional) Primary Language, Custom Project Notes.
  * Clicking "Add Project & Setup Webhook" will add the project to NovaGuard and attempt to automatically create a webhook on the GitHub repository for PR events.
      * **Note:** For automatic webhook creation to succeed, your `NOVAGUARD_PUBLIC_URL` environment variable must be correctly set to a URL reachable by GitHub.com (e.g., an ngrok tunnel URL during local development).

### 4\. Automatic PR Analysis

  * Once a project is added and the webhook is active on GitHub:
      * When a Pull Request is opened, reopened, or synchronized (new commits pushed) in the linked GitHub repository, GitHub will send an event to NovaGuard-AI.
      * NovaGuard-AI will queue an analysis task.
      * The `novaguard_analysis_worker` will pick up the task, fetch PR data, invoke the LLM for analysis, and save the findings.

### 5\. View Analysis Reports

  * From the Dashboard, click on a project name to go to its "Project Detail" page.
  * This page lists the history of Pull Request analyses for that project.
  * Click "View Report" next to a specific PR analysis to see the detailed findings, including descriptions, suggestions, and relevant code snippets.

### 6\. Delete a Project

  * Navigate to the "Project Settings" page for the project you wish to delete.
  * In the "Danger Zone" section, click "Delete This Project". You will be asked for confirmation.
  * This action removes the project and its analysis history from NovaGuard and attempts to delete the associated webhook from GitHub.

## Environment Variables Details

The following environment variables are configured in `novaguard-backend/.env` (copied from `.env.example`):

  * **`DATABASE_URL`**: PostgreSQL connection string.
      * Default for Docker: `postgresql://novaguard_user:novaguard_password@postgres_db:5432/novaguard_db`
  * **`SECRET_KEY`**: A strong secret key for signing JWTs. **Generate a secure random key for this.**
      * Example generation: `openssl rand -hex 32`
  * **`ALGORITHM`**: JWT signing algorithm (default: `HS256`).
  * **`ACCESS_TOKEN_EXPIRE_MINUTES`**: JWT access token lifetime in minutes (default: `1440` for 1 day).
  * **`FERNET_ENCRYPTION_KEY`**: A Fernet key for encrypting sensitive data like GitHub access tokens. **Generate a secure key.**
      * Example generation (Python): `from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())`
  * **`GITHUB_CLIENT_ID`**: Your GitHub OAuth App's Client ID.
  * **`GITHUB_CLIENT_SECRET`**: Your GitHub OAuth App's Client Secret.
  * **`GITHUB_REDIRECT_URI`**: The callback URL for your GitHub OAuth App.
      * Default for local dev: `http://localhost:8000/api/auth/github/callback` (Ensure this matches your GitHub OAuth App config).
  * **`GITHUB_WEBHOOK_SECRET`**: A strong secret string used to secure and verify payloads from GitHub webhooks. **Generate a secure random string.**
  * **`SESSION_SECRET_KEY`**: A secret key for signing user session cookies (FastAPI's `SessionMiddleware`). **Generate a secure random key.**
  * **`KAFKA_BOOTSTRAP_SERVERS`**: Comma-separated list of Kafka brokers.
      * Default for Docker: `kafka:29092`
  * **`KAFKA_PR_ANALYSIS_TOPIC`**: Kafka topic name for PR analysis tasks (default: `pr_analysis_tasks`).
  * **`OLLAMA_BASE_URL`**: Base URL for the Ollama API service.
      * Default for Docker: `http://ollama:11434`
  * **`OLLAMA_DEFAULT_MODEL`**: Default LLM model to use with Ollama (e.g., `codellama:7b-instruct-q4_K_M`).
  * **`NOVAGUARD_PUBLIC_URL`**: The publicly accessible base URL of your NovaGuard-AI instance. This is crucial for GitHub webhooks to reach your application, especially during local development (use ngrok or similar).
      * Example: `https://your-ngrok-subdomain.ngrok-free.app` or `https://novaguard.yourcompany.com`
  * **`DEBUG`**: Set to `True` for development mode (more verbose logging, debug features), `False` for production (default: `False`).

## Development

### Running Tests

Tests are located in the `novaguard-backend/tests/` directory.
Ensure your Python environment is set up with all dependencies from `requirements.txt`.
From the `novaguard-backend` directory:

```bash
# Ensure PYTHONPATH includes the app directory if needed
# export PYTHONPATH=$(pwd):$PYTHONPATH 

# Discover and run all tests
python -m unittest discover -s tests

# Run tests for a specific module
python -m unittest tests.core.test_config
```

### Prompts

LLM prompts are stored as Markdown files in `novaguard-backend/app/prompts/`. Currently, `deep_logic_bug_hunter_v1.md` is used for the primary code analysis agent. These prompts utilize placeholders (e.g., `{pr_title}`, `{format_instructions}`) that are filled in by the `analysis_worker` using Langchain's `ChatPromptTemplate`.

## Future Roadmap (Post-MVP1 Highlights)

(Based on `DESIGN.MD`)

  * **Full Project Scans:** Analyze entire codebases, not just PRs.
  * **Code Knowledge Graph (CKG):** Build and utilize a CKG (e.g., with Neo4j, tree-sitter) for deeper contextual understanding and more advanced architectural analysis.
  * **Additional Specialized Agents:**
      * `ArchitecturalAnalystAI` (more advanced)
      * `SecuritySentinelAI`
      * `PerformanceProfilerAI`
  * **Enhanced Project Configuration:** Allow users to select LLM models per agent, define custom coding conventions and architectural rules.
  * **Technical Debt Tracking.**
  * **Project-Specific Knowledge Base.**
  * **Frontend SPA:** Potential migration to a Single Page Application (React/Vue/Angular) for a richer user experience.
  * **Support for other Git platforms** (GitLab, Bitbucket).

## Contributing

Details on contributing will be added here. (Standard contribution guidelines: forking, branching, PRs, code style, tests).

## License

MIT

