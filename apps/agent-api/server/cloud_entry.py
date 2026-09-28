"""Cloud entrypoint. Environment must be selected before importing the business modules."""
import os
os.environ['ENGINEERING_RUNTIME'] = 'cloud'

from .main import app as business_app
from .cloud_app import CloudApplication

app = CloudApplication(business_app)
