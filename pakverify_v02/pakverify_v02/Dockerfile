# Use official Python image
FROM python:3.10

# Hugging Face requires a specific user for security permissions
RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH"

WORKDIR /app

# Copy and install your requirements
COPY --chown=user ./requirements.txt requirements.txt
RUN pip install --no-cache-dir --upgrade -r requirements.txt

# Copy all your project code
COPY --chown=user . /app

# Boot up FastAPI on the exact port Hugging Face requires
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]