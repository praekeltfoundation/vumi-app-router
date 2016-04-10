FROM praekeltfoundation/vumi
ENV WORKER_CLASS "vxapprouter.router.ApplicationDispatcher"

COPY . /app
WORKDIR /app
RUN pip install -e .
ENV CONFIG_FILE "vxapprouter.yaml"

EXPOSE 8000
