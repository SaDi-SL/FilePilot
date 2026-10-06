import math
import unittest

from app.semantic_search import (
    SemanticSearchError,
    cosine_similarity,
    decode_vector,
    embedding_fingerprint,
    encode_vector,
    normalize_vector,
)


class SemanticSearchTests(unittest.TestCase):
    def test_normalize_vector_returns_unit_length(self):
        vector = normalize_vector([3.0, 4.0])
        self.assertAlmostEqual(math.sqrt(sum(value * value for value in vector)), 1.0)

    def test_zero_and_non_finite_vectors_are_rejected(self):
        with self.assertRaises(SemanticSearchError):
            normalize_vector([0.0, 0.0])
        with self.assertRaises(SemanticSearchError):
            normalize_vector([1.0, float("nan")])

    def test_encode_decode_round_trip_is_bounded_float32(self):
        payload, dimensions = encode_vector([1.0, 2.0, 3.0])
        decoded = decode_vector(payload, dimensions)
        self.assertEqual(dimensions, 3)
        self.assertEqual(len(decoded), 3)
        self.assertAlmostEqual(sum(value * value for value in decoded), 1.0, places=5)

    def test_cosine_similarity_prefers_same_direction(self):
        self.assertAlmostEqual(cosine_similarity([1, 0], [2, 0]), 1.0)
        self.assertAlmostEqual(cosine_similarity([1, 0], [0, 1]), 0.0)

    def test_embedding_fingerprint_changes_with_model_or_dimensions(self):
        first = embedding_fingerprint(provider="ollama", model="embed-a", dimensions=3)
        same = embedding_fingerprint(provider="OLLAMA", model="embed-a", dimensions=3)
        other_model = embedding_fingerprint(provider="ollama", model="embed-b", dimensions=3)
        other_dimensions = embedding_fingerprint(provider="ollama", model="embed-a", dimensions=4)

        self.assertEqual(first, same)
        self.assertNotEqual(first, other_model)
        self.assertNotEqual(first, other_dimensions)


if __name__ == "__main__":
    unittest.main()
