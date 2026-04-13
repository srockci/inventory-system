FROM python:3.11-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用
COPY . .

# 初始化数据库（只在首次创建时运行，已创建的会跳过）
RUN python init_db.py

EXPOSE 5000

# 启动
CMD ["python", "app.py"]
