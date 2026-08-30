"""A DevOps question-answering app over a graph database.

Runs on Neo4j or on Axon. The same Cypher and the same driver serve both; the
backends differ only where the databases genuinely differ, which is the
interesting part — see backend.py.
"""
__version__ = "0.1.0"
