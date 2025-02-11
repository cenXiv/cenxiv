import os
from celery import Celery
from decouple import config


rb_user = config('RABBITMQ_USER', default='guest'))
rb_passwd = config('RABBITMQ_PASSWORD', default='guest'))
rb_location = config('RABBITMQ_LOCATION', default='localhost:5672')


# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'cenxiv.settings')

# app = Celery('cenxiv', broker='amqp://guest:guest@localhost')
app = Celery('cenxiv', broker=f'amqp://{rb_user}:{rb_passwd}@{rb_location}')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object(f'django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django apps.
app.autodiscover_tasks()