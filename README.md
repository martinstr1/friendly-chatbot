# Friendly-chatbot

Friendly-chatbot is a minimal Flask web application that demonstrates a continuous delivery workflow to Google Cloud Run. The service exposes a couple of simple endpoints that make it easy to verify that the latest container image has been deployed successfully and that secrets are available inside the running environment.

## Features
- **Root endpoint (`/`)** returns a confirmation string (`"Hello, Cloud Run! (CI/CD OK)"`). Use it to quickly ensure the application is responding after each deployment.
- **Ping endpoint (`/ping`)** checks whether the `SECRET_VALUE` environment variable is present. The endpoint responds with JSON that notes if the secret is set, so you can validate your Cloud Run secret integration.

## Requirements
- Python 3.11+
- pip

## Local development
1. Create a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   # PowerShell
   . .\.venv\Scripts\Activate.ps1
   # Git Bash / Unix shells
   # source .venv/Scripts/activate  # Windows
   # source .venv/bin/activate      # macOS/Linux

   pip install -r requirements.txt
   ```
2. Start the Flask development server:
   ```bash
   python app/main.py
   ```
3. Open <http://localhost:8080/> in your browser to confirm the root endpoint is reachable.

## Environment variables
The application reads an optional environment variable:

| Name           | Purpose                                             |
| -------------- | --------------------------------------------------- |
| `SECRET_VALUE` | Used by the `/ping` endpoint to verify secret access |

When running in Cloud Run you can provide the value via Secret Manager bindings so the `/ping` endpoint reports that the secret is available.

## Deployment
This repository includes a `cloudbuild.yaml` file that builds and deploys the container to Cloud Run. A typical workflow is:

1. Configure Google Cloud Build triggers to watch the repository.
2. When changes are pushed, Cloud Build uses the `Dockerfile` to build a new image and deploys it to Cloud Run using the settings defined in `cloudbuild.yaml`.
3. Verify the deployment by visiting the service URL or checking the `/ping` endpoint for secret availability.

Refer to the Google Cloud Run documentation for detailed setup steps around enabling APIs, configuring service accounts, and managing secrets.

## Branch availability
- `work`: Primary development branch for ongoing feature work.
- `intelligence`: Auxiliary branch created to support Codex workflows that require a dedicated branch name.
