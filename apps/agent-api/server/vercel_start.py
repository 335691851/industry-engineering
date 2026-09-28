"""Container entrypoint respects the hosting platform's assigned port."""
import os
import uvicorn

if __name__ == '__main__':
    uvicorn.run('server.cloud_entry:app', host='0.0.0.0', port=int(os.getenv('PORT','8080')))
