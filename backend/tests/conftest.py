"""All tests use a disposable database and never inherit real model credentials."""
import os
import tempfile

TEST_DATA=tempfile.mkdtemp(prefix='folio-test-')
os.environ['DATA_DIR']=TEST_DATA
os.environ['DATABASE_URL']=f'sqlite:///{TEST_DATA}/test.db'
for key in ['ZHIPU_API_KEY','LLM_API_KEY','LLM_MODEL','EMBEDDING_API_KEY','EMBEDDING_MODEL']:
    os.environ[key]=''
