#!/bin/bash
set -eu

echo "Waiting for Kafka to be ready..."
until kafka-topics --bootstrap-server "$KAFKA_BOOTSTRAP_SERVERS" --list >/dev/null 2>&1; do
  sleep 1
  echo -n "."
done

echo "Kafka is ready! Creating topics..."

for TOPIC in spaider.ingest.raw spaider.ingest.dlq spaider.workflow.events; do
  echo "Creating topic: $TOPIC"
  kafka-topics --bootstrap-server "$KAFKA_BOOTSTRAP_SERVERS" \
    --create \
    --if-not-exists \
    --topic "$TOPIC" \
    --partitions 3 \
    --replication-factor 1 \
    --config retention.ms=604800000 \
    --config cleanup.policy=delete \
    --config min.insync.replicas=1
  echo "Topic $TOPIC created successfully"
done

echo "All topics created successfully"
exit 0
