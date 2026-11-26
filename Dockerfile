# Runs only the read-only MCP server (`tidywise mcp`), e.g. for Glama's inspection.
# The container has no archive of its own, so the tools answer that it is still empty;
# for real use, install Tidywise on the Mac (see README).
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY tidywise ./tidywise
RUN pip install --no-cache-dir . \
    && useradd --create-home tidywise

USER tidywise
ENTRYPOINT ["tidywise", "mcp", "--always"]
