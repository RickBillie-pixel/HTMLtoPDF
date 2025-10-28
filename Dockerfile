# Dockerfile
FROM python:3.11-slim

# Installeer Gotenberg dependencies
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Download en installeer Gotenberg
RUN wget https://github.com/gotenberg/gotenberg/releases/download/v7.10.1/gotenberg_7.10.1_linux_amd64.deb \
    && dpkg -i gotenberg_7.10.1_linux_amd64.deb \
    && rm gotenberg_7.10.1_linux_amd64.deb

# Werkdirectory
WORKDIR /app

# Kopieer requirements en installeer
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Kopieer applicatie code
COPY main.py .

# Maak directory voor PDFs
RUN mkdir -p generated_pdfs

# Start script maken
RUN echo '#!/bin/bash\n\
gotenberg --api-port=3000 &\n\
sleep 3\n\
uvicorn main:app --host 0.0.0.0 --port 8000' > /start.sh && \
chmod +x /start.sh

# Expose ports
EXPOSE 8000

# Start beide services
CMD ["/start.sh"]
