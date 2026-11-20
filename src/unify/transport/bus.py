"""Abstraction du bus de messages (Kafka en PRD, mémoire en tests / démo).

Les producteurs et consommateurs du pipeline ne dépendent que de l'interface
``MessageBus`` : on teste toute la chaîne sans broker, et on bascule sur Kafka
(``UNIFY_BUS=kafka``) sans modifier le code métier (principe d'inversion de dépendance).

Garanties Kafka retenues : producteur idempotent (``enable.idempotence``), ``acks=all``,
clé de message = identifiant source (ordre garanti par entité), consommateur en
« at-least-once » (commit après écriture) -> les traitements aval DOIVENT être idempotents.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Iterable, Iterator

from unify.config import Settings


class MessageBus(ABC):
    @abstractmethod
    def publish(self, topic: str, events: Iterable[dict], key_field: str | None = None) -> int:
        """Publie des événements ; retourne le nombre de messages envoyés."""

    @abstractmethod
    def consume(self, topic: str) -> Iterator[dict]:
        """Lit les événements d'un topic depuis l'offset engagé du groupe de consommateurs."""


class InMemoryBus(MessageBus):
    """Bus en mémoire : un topic = une liste ordonnée de messages sérialisés en JSON."""

    def __init__(self) -> None:
        self.topics: dict[str, list[bytes]] = defaultdict(list)
        self.offsets: dict[str, int] = defaultdict(int)

    def publish(self, topic: str, events: Iterable[dict], key_field: str | None = None) -> int:
        count = 0
        for event in events:
            self.topics[topic].append(json.dumps(event, ensure_ascii=False).encode())
            count += 1
        return count

    def consume(self, topic: str) -> Iterator[dict]:
        messages = self.topics.get(topic, [])
        start = self.offsets[topic]
        for raw in messages[start:]:
            yield json.loads(raw)
        self.offsets[topic] = len(messages)

    def rewind(self, topic: str) -> None:
        """Rejoue un topic depuis le début (équivalent d'un reset d'offset Kafka)."""
        self.offsets[topic] = 0


class KafkaBus(MessageBus):  # pragma: no cover - nécessite un broker Kafka (docker compose)
    def __init__(self, settings: Settings, group_id: str = "unify-bronze-writer") -> None:
        from confluent_kafka import Consumer, Producer

        self.producer = Producer(
            {
                "bootstrap.servers": settings.kafka_bootstrap,
                "enable.idempotence": True,
                "acks": "all",
                "compression.type": "zstd",
            }
        )
        self._consumer_conf = {
            "bootstrap.servers": settings.kafka_bootstrap,
            "group.id": group_id,
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
        self._consumer_cls = Consumer

    def publish(self, topic: str, events: Iterable[dict], key_field: str | None = None) -> int:
        count = 0
        for event in events:
            after = event.get("after") or {}
            key = str(after.get(key_field)) if key_field and after else None
            self.producer.produce(topic, key=key, value=json.dumps(event).encode())
            count += 1
            if count % 10_000 == 0:
                self.producer.flush()
        self.producer.flush()
        return count

    def consume(self, topic: str, idle_timeout: float = 5.0) -> Iterator[dict]:
        consumer = self._consumer_cls(self._consumer_conf)
        consumer.subscribe([topic])
        try:
            while True:
                msg = consumer.poll(idle_timeout)
                if msg is None:
                    break
                if msg.error():
                    raise RuntimeError(msg.error())
                yield json.loads(msg.value())
                consumer.commit(msg, asynchronous=False)
        finally:
            consumer.close()


def get_bus(settings: Settings) -> MessageBus:
    if settings.bus == "kafka":  # pragma: no cover
        return KafkaBus(settings)
    return InMemoryBus()
