FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py crowd_map.html road_network.json road_graph.json mcp_server.py ./
COPY tiles/ tiles/
EXPOSE 8765
CMD ["python", "server.py"]
