FROM python:3.12-slim

# Set work directory
WORKDIR /app

# Set environment variable for Django settings
ENV DJANGO_SETTINGS_MODULE=app.settings.prod

# Install Python dependencies
# Copy only requirements first to leverage Docker cache if requirements.txt hasn't changed
COPY requirements.txt /app/
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy the rest of the application code
COPY . /app/

# Expose port 8000 (adjust if you use a different port)
EXPOSE 8000

# Command to run the Daphne server for your ASGI application.
# Replace 'project' with your Django project name if different.
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "app.asgi:application"]
