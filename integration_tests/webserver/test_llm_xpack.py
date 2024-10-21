# flake8: noqa
# ignore the imports
import os
import pathlib
from typing import Any, List, TypeAlias

import openapi_spec_validator
import pytest
import requests
from langchain.text_splitter import CharacterTextSplitter
from langchain_core.embeddings import Embeddings
from llama_index.core.embeddings import BaseEmbedding
from llama_index.core.node_parser import TextSplitter
from llama_index.readers.pathway import PathwayReader
from llama_index.retrievers.pathway import PathwayRetriever

import pathway as pw
from pathway.tests.utils import wait_result_with_checker
from pathway.xpacks.llm import llms
from pathway.xpacks.llm.question_answering import BaseRAGQuestionAnswerer
from pathway.xpacks.llm.vector_store import VectorStoreClient, VectorStoreServer

PATHWAY_HOST = "127.0.0.1"


class LangChainFakeEmbeddings(Embeddings):
    def embed_query(self, text: str) -> list[float]:
        return [1.0, 1.0, 1.0 if text == "foo" else -1.0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]


def pathway_server_from_langchain(tmp_path, port):
    data_sources = []
    data_sources.append(
        pw.io.fs.read(
            tmp_path,
            format="binary",
            mode="streaming",
            with_metadata=True,
        )
    )

    embeddings_model = LangChainFakeEmbeddings()
    splitter = CharacterTextSplitter("\n\n", chunk_size=4, chunk_overlap=0)

    vector_server = VectorStoreServer.from_langchain_components(
        *data_sources, embedder=embeddings_model, splitter=splitter
    )
    thread = vector_server.run_server(
        host=PATHWAY_HOST,
        port=port,
        threaded=True,
        with_cache=False,
    )
    thread.join()


def test_llm_xpack_autogenerated_docs_validity(tmp_path: pathlib.Path, port: int):

    def checker() -> bool:
        description = None
        try:
            schema = requests.get(
                f"http://{PATHWAY_HOST}:{port}/_schema?format=json", timeout=1
            )
            schema.raise_for_status()
            description = schema.json()
            assert description is not None
            openapi_spec_validator.validate(description)
        except Exception:
            return False

        return True

    wait_result_with_checker(
        checker, 20, target=pathway_server_from_langchain, args=[tmp_path, port]
    )


def test_similarity_search_without_metadata(tmp_path: pathlib.Path, port: int):
    with open(tmp_path / "file_one.txt", "w+") as f:
        f.write("foo")

    client = VectorStoreClient(host=PATHWAY_HOST, port=port)

    def checker() -> bool:
        output = []
        try:
            output = client("foo")
        except requests.exceptions.RequestException:
            return False
        return (
            len(output) == 1
            and output[0]["dist"] < 0.0001
            and output[0]["text"] == "foo"
            and "metadata" in output[0]
        )

    wait_result_with_checker(
        checker, 20, target=pathway_server_from_langchain, args=[tmp_path, port]
    )


def test_vector_store_with_langchain(tmp_path: pathlib.Path, port) -> None:
    with open(tmp_path / "file_one.txt", "w+") as f:
        f.write("foo\n\nbar")

    client = VectorStoreClient(host=PATHWAY_HOST, port=port)

    def checker() -> bool:
        output = []
        try:
            output = client.query("foo", 1, filepath_globpattern="**/file_one.txt")
        except requests.exceptions.RequestException:
            return False

        return len(output) == 1 and output[0]["text"] == "foo"

    wait_result_with_checker(
        checker, 20, target=pathway_server_from_langchain, args=[tmp_path, port]
    )


EXAMPLE_TEXT_FILE = "example_text.md"


def get_data_sources():
    test_dir = os.path.dirname(os.path.abspath(__file__))
    example_text_path = os.path.join(test_dir, EXAMPLE_TEXT_FILE)

    data_sources = []
    data_sources.append(
        pw.io.fs.read(
            example_text_path,
            format="binary",
            mode="streaming",
            with_metadata=True,
        )
    )
    return data_sources


def mock_get_text_embedding(text: str) -> List[float]:
    """Mock get text embedding."""
    if text == "Hello world.":
        return [1.0, 0.0, 0.0, 0.0, 0.0]
    elif text == "This is a test.":
        return [0.0, 1.0, 0.0, 0.0, 0.0]
    elif text == "This is another test.":
        return [0.0, 0.0, 1.0, 0.0, 0.0]
    elif text == "This is a test v2.":
        return [0.0, 0.0, 0.0, 1.0, 0.0]
    elif text == "This is a test v3.":
        return [0.0, 0.0, 0.0, 0.0, 1.0]
    elif text == "This is bar test.":
        return [0.0, 0.0, 1.0, 0.0, 0.0]
    elif text == "Hello world backup.":
        return [0.0, 0.0, 0.0, 0.0, 1.0]
    else:
        return [0.0, 0.0, 0.0, 0.0, 0.0]


class NewlineTextSplitter(TextSplitter):
    def split_text(self, text: str) -> List[str]:
        return text.split(",")


class LlamaIndexFakeEmbedding(BaseEmbedding):
    def _get_text_embedding(self, text: str) -> List[float]:
        return mock_get_text_embedding(text)

    def _get_query_embedding(self, query: str) -> List[float]:
        return mock_get_text_embedding(query)

    async def _aget_query_embedding(self, query: str) -> List[float]:
        return mock_get_text_embedding(query)


def pathway_server_from_llama_index(port):
    data_sources = get_data_sources()

    embed_model = LlamaIndexFakeEmbedding()

    custom_transformations = [
        NewlineTextSplitter(),
        embed_model,
    ]

    processing_pipeline = VectorStoreServer.from_llamaindex_components(
        *data_sources,
        transformations=custom_transformations,
    )

    thread = processing_pipeline.run_server(
        host=PATHWAY_HOST,
        port=port,
        threaded=True,
        with_cache=False,
    )
    thread.join()


def test_llama_retriever(port: int):
    retriever = PathwayRetriever(host=PATHWAY_HOST, port=port, similarity_top_k=1)

    def checker() -> bool:
        results = []
        try:
            results = retriever.retrieve(str_or_query_bundle="Hello world.")
        except requests.exceptions.RequestException:
            return False

        return (
            len(results) == 1
            and results[0].text == "Hello world."
            and results[0].score == 1.0
        )

    wait_result_with_checker(
        checker, 20, target=pathway_server_from_llama_index, args=[port]
    )


def test_llama_reader(port: int):
    pr = PathwayReader(host=PATHWAY_HOST, port=port)

    def checker() -> bool:
        results = []
        try:
            results = pr.load_data("Hello world.", k=1)
        except requests.exceptions.RequestException:
            return False

        if not (
            len(results) == 1
            and results[0].text == "Hello world."
            and EXAMPLE_TEXT_FILE in results[0].metadata["path"]
        ):
            return False

        results = []
        try:
            results = pr.load_data("This is a test.", k=1)
        except requests.exceptions.RequestException:
            return False

        return (
            len(results) == 1
            and results[0].text == "This is a test."
            and EXAMPLE_TEXT_FILE in results[0].metadata["path"]
        )

    wait_result_with_checker(
        checker, 20, target=pathway_server_from_llama_index, args=[port]
    )


def build_vector_store(embedder) -> VectorStoreServer:
    """From a given embedder, with a single doc."""
    docs = pw.debug.table_from_rows(
        schema=pw.schema_from_types(data=bytes, _metadata=dict),
        rows=[
            (
                "test".encode("utf-8"),
                {"path": "test_module.py"},
            )
        ],
    )

    vector_server = VectorStoreServer(
        docs,
        embedder=embedder,
    )

    return vector_server


@pytest.mark.parametrize(
    "cache_strategy_cls",
    [
        None,
        pw.udfs.InMemoryCache,
        pw.udfs.DiskCache,
    ],
)
def test_vectorstore_builds(port: int, cache_strategy_cls):
    if cache_strategy_cls is not None:
        cache_strategy = cache_strategy_cls()
    else:
        cache_strategy = None

    @pw.udf(cache_strategy=cache_strategy)
    def fake_embeddings_model(x: str) -> list[float]:
        return [1.0, 1.0, 0.0]

    indexer = build_vector_store(fake_embeddings_model)

    def checker() -> bool:
        try:
            client = VectorStoreClient(host=PATHWAY_HOST, port=port)
            inputs = client.get_input_files()

            assert len(inputs) == 1
        except Exception:
            return False

        return True

    wait_result_with_checker(
        checker,
        20,
        target=indexer.run_server,
        kwargs=dict(
            host=PATHWAY_HOST,
            port=port,
            with_cache=True,
        ),
    )


def build_rag_app(port: int) -> BaseRAGQuestionAnswerer:
    @pw.udf
    def fake_embeddings_model(x: str) -> list[float]:
        return [1.0, 1.0, 0.0]

    class FakeChatModel(llms.BaseChat):
        async def __wrapped__(self, *args, **kwargs) -> str:
            return "Text"

        def _accepts_call_arg(self, arg_name: str) -> bool:
            return True

    chat = FakeChatModel()

    vector_server = build_vector_store(fake_embeddings_model)

    rag_app = BaseRAGQuestionAnswerer(
        llm=chat,
        indexer=vector_server,
        default_llm_name="gpt-4o-mini",
    )

    rag_app.build_server(host=PATHWAY_HOST, port=port)

    return rag_app


@pytest.mark.parametrize("input", [1, 2, 3, 99])
@pytest.mark.parametrize(
    "async_mode",
    [False, True],
)
def test_serve_callable(port: int, input: int, async_mode: bool):
    TEST_ENDPOINT = "test_add_1"
    expected = input + 1

    rag_app = build_rag_app(port)

    if async_mode:

        @rag_app.serve_callable(
            route=f"/{TEST_ENDPOINT}", schema=pw.schema_from_types(input=int)
        )
        async def increment(input: int) -> int:
            return input + 1

    else:

        @rag_app.serve_callable(
            route=f"/{TEST_ENDPOINT}", schema=pw.schema_from_types(input=int)
        )
        def increment(input: int) -> int:
            return input + 1

    def checker() -> bool:
        try:
            response = requests.post(
                f"http://{PATHWAY_HOST}:{port}/{TEST_ENDPOINT}",
                json={"input": input},
                timeout=4,
            )
            result = response.json()

            assert expected == result
        except Exception:
            return False

        return True

    wait_result_with_checker(
        checker,
        20,
        target=rag_app.run_server,
    )


@pytest.mark.parametrize(
    "input", [1, None, {"a": "b"}, {"a": {"b": "c"}}, [1, 2, 3], "str"]
)
def test_serve_callable_symmetric(port: int, input: Any):
    TEST_ENDPOINT = "symmetric"
    expected = input

    rag_app = build_rag_app(port)

    UType: TypeAlias = int | dict | str | list | None

    @rag_app.serve_callable(route=f"/{TEST_ENDPOINT}")
    async def symmetric_fn(
        input: UType,
    ) -> UType:
        return input

    def checker() -> bool:
        retries = 3
        for _ in range(retries):
            try:
                response = requests.post(
                    f"http://{PATHWAY_HOST}:{port}/{TEST_ENDPOINT}",
                    json={"input": input},
                    timeout=4,
                )
                result = response.json()

                assert expected == result
                return True
            except requests.exceptions.Timeout:
                continue
            except Exception:
                return False
        return False

    wait_result_with_checker(
        checker,
        20,
        target=rag_app.run_server,
    )


@pytest.mark.parametrize("dc", [{"l": 3, "k": "2", "nested": {"a": "b"}}])
@pytest.mark.parametrize("name", ["name"])
@pytest.mark.parametrize("typed", [True, False])
@pytest.mark.parametrize(
    "schema", [None, pw.schema_from_types(request_dc=dict, request_name=str)]
)
def test_serve_callable_nested_async_typing(
    port: int, dc: dict, name: str, typed: bool, schema: type[pw.Schema] | None
):
    TEST_ENDPOINT = "nested"
    expected = [{"name": name, "value": dc}]

    rag_app = build_rag_app(port)

    if typed:

        @rag_app.serve_callable(route=f"/{TEST_ENDPOINT}", schema=schema)
        async def embed_dict(request_dc: dict, request_name: str) -> list[dict]:
            return [{"name": request_name, "value": request_dc}]

    else:

        @rag_app.serve_callable(route=f"/{TEST_ENDPOINT}", schema=schema)
        async def embed_dict(request_dc, request_name):
            return [{"name": request_name, "value": request_dc}]

    def checker() -> bool:
        retries = 3
        for _ in range(retries):
            try:
                response = requests.post(
                    f"http://{PATHWAY_HOST}:{port}/{TEST_ENDPOINT}",
                    json={"request_dc": dc, "request_name": name},
                    timeout=6,
                )
                result = response.json()

                assert expected == result
                return True
            except requests.exceptions.Timeout:
                continue
            except Exception:
                return False

        return True

    wait_result_with_checker(
        checker,
        20,
        target=rag_app.run_server,
    )


def test_serve_callable_with_search(port: int):
    TEST_ENDPOINT = "custom_search"
    expected = "test"  # set in the docs part of `build_rag_app`

    rag_app = build_rag_app(port)

    @rag_app.serve_callable(route=f"/{TEST_ENDPOINT}")
    async def return_top_doc_text(query):
        vs_client = VectorStoreClient(host=PATHWAY_HOST, port=port)
        return vs_client.query(query, k=1)[0]["text"]

    def checker() -> bool:
        retries = 3
        for _ in range(retries):
            try:
                response = requests.post(
                    f"http://{PATHWAY_HOST}:{port}/{TEST_ENDPOINT}",
                    json={"query": "test"},
                    timeout=4,
                )
                result = response.json()

                assert expected == result
                return True
            except requests.exceptions.Timeout:
                continue
            except Exception:
                return False
        return False

    wait_result_with_checker(
        checker,
        20,
        target=rag_app.run_server,
    )
