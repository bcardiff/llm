from .models import EmbeddingModel
from .embeddings_migrations import embeddings_migrations
import json
from sqlite_utils import Database
from sqlite_utils.db import Table
from typing import cast, Any, Dict, List, Tuple, Optional, Union


class Collection:
    def __init__(
        self,
        db: Database,
        name: str,
        *,
        model: Optional[EmbeddingModel] = None,
        model_id: Optional[str] = None,
    ) -> None:
        """
        Initialization for the Collection Class.

        :param db: The input database
        :param name: Name of the collection.
        :param model: The embedding model. Defaults to None.
        :param model_id: ID of the model. Defaults to None.

        :exception ValueError: Raises ValueError if `model_id` does not match `model.model_id`.
        """
        self.db = db
        self.name = name
        if model and model_id and model.model_id != model_id:
            raise ValueError("model_id does not match model.model_id")
        self._model = model
        self._model_id = model_id
        self._id = None
        self._id = self.id()

    def model(self) -> EmbeddingModel:
        """
        Returns the model of the collection.

        :exception ValueError: Raises ValueError if no model_id specified and no model found with that name.

        :returns: The collection's model.
        """
        import llm

        if self._model:
            return self._model
        try:
            self._model = llm.get_embedding_model(self._model_id)
        except llm.UnknownModelError:
            raise ValueError("No model_id specified and no model found with that name")
        return cast(EmbeddingModel, self._model)

    def id(self) -> int:
        """
        Returns the collection's ID, creating it in the database if necessary.

        :returns: ID of the collection.
        """
        if self._id is not None:
            return self._id
        if not self.db["collections"].exists():
            embeddings_migrations.apply(self.db)
        rows = self.db["collections"].rows_where("name = ?", [self.name])
        try:
            row = next(rows)
            self._id = row["id"]
            if self._model_id is None:
                self._model_id = row["model"]
        except StopIteration:
            # Create it
            self._id = (
                cast(Table, self.db["collections"])
                .insert(
                    {
                        "name": self.name,
                        "model": self.model().model_id,
                    }
                )
                .last_pk
            )
        return cast(int, self._id)

    def exists(self) -> bool:
        """
        Checks if the collection exists in the database.

        :returns: True if exists, False otherwise.
        """
        matches = list(
            self.db.query("select 1 from collections where name = ?", (self.name,))
        )
        return bool(matches)

    def count(self) -> int:
        """
        Counts the number of items in the collection.

        :returns: Number of items in the collection.
        """
        return next(
            self.db.query(
                """
            select count(*) as c from embeddings where collection_id = (
                select id from collections where name = ?
            )
            """,
                (self.name,),
            )
        )["c"]

    def embed(
        self,
        id: str,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
        store: bool = False,
    ) -> None:
        """
        Embed a text and store it in the collection with a given ID.

        :param id: ID for the text.
        :param text: Text to be embedded.
        :param metadata: Metadata to be stored. Defaults to None.
        :param store: Whether to store the text in the content column. Defaults to False.
        """
        from llm import encode

        embedding = self.model().embed(text)
        cast(Table, self.db["embeddings"]).insert(
            {
                "collection_id": self.id(),
                "id": id,
                "embedding": encode(embedding),
                "content": text if store else None,
                "metadata": json.dumps(metadata) if metadata else None,
            }
        )

    def embed_multi(self, id_text_map: Dict[str, str], store: bool = False) -> None:
        """
        Embed multiple texts and store them in the collection with given IDs.

        :param id_text_map: Dictionary mapping IDs to texts.
        :param store: Whether to store the text in the content column. Defaults to False.
        """
        raise NotImplementedError

    def embed_multi_with_metadata(
        self,
        id_text_metadata_map: Dict[str, Tuple[str, Dict[str, Union[str, int, float]]]],
    ) -> None:
        """
        Embed multiple texts along with metadata and store them in the collection with given IDs.

        :param id_text_metadata_map: Dictionary mapping IDs to (text, metadata) tuples.
        """
        raise NotImplementedError

    def similar_by_vector(
        self, vector: List[float], number: int = 5, skip_id: Optional[str] = None
    ) -> List[Tuple[str, float]]:
        """
        Finds similar items in the collection by a given vector.

        :param vector: Vector to search by.
        :param number: Number of similar items to return. Defaults to 5.
        :param skip_id: ID to be skipped. Defaults to None.

        :returns: List of (id, score) tuples.
        """
        import llm

        def distance_score(other_encoded):
            other_vector = llm.decode(other_encoded)
            return llm.cosine_similarity(other_vector, vector)

        self.db.register_function(distance_score, replace=True)

        where_bits = ["collection_id = ?"]
        where_args = [str(self.id())]

        if skip_id:
            where_bits.append("id != ?")
            where_args.append(skip_id)

        return [
            (row["id"], row["score"])
            for row in self.db.query(
                """
            select id, distance_score(embedding) as score
            from embeddings
            where {where}
            order by score desc limit {number}
        """.format(
                    where=" and ".join(where_bits),
                    number=number,
                ),
                where_args,
            )
        ]

    def similar_by_id(self, id: str, number: int = 5) -> List[Tuple[str, float]]:
        """
        Finds similar items in the collection by a given ID.

        :param id: ID to search by.
        :param number: Number of similar items to return. Defaults to 5.

        :exception ValueError: Raises ValueError if ID not found.

        :returns: List of (id, score) tuples.
        """
        import llm

        matches = list(
            self.db["embeddings"].rows_where(
                "collection_id = ? and id = ?", (self.id(), id)
            )
        )
        if not matches:
            raise ValueError("ID not found")
        embedding = matches[0]["embedding"]
        comparison_vector = llm.decode(embedding)
        return self.similar_by_vector(comparison_vector, number, skip_id=id)

    def similar(self, text: str, number: int = 5) -> List[Tuple[str, float]]:
        """
        Finds similar items in the collection by a given text.

        :param text: Text to search by.
        :param number: Number of similar items to return. Defaults to 5.

        :returns: List of (id, score) tuples.
        """
        comparison_vector = self.model().embed(text)
        return self.similar_by_vector(comparison_vector, number)
