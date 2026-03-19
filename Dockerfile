FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir networkx
COPY server.py crowd_map.html road_network.json road_graph.json ./
COPY tiles/ tiles/
EXPOSE 8765
CMD ["python", "server.py"]
