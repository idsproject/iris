import boto3
import random
import time
import urllib.request
import urllib.error

# Name/path of the Secrets Manager secret holding the IRIS endpoint + API key.
# Matches the /myapp/* prefix convention used for the Adobe credentials.
# Secret should be a JSON object: {"url": "...", "api_key": "..."}
IRIS_SECRET_NAME = "/myapp/iris-notification"

_iris_config_cache = None  # cached for the lifetime of the Lambda execution environment

# Helper function for exponential backoff and retry
def exponential_backoff_retry(
    func,
    *args,
    retries=3,
    base_delay=1,
    backoff_factor=2,
    **kwargs
):
    """
    Retries a given function using exponential backoff in case of exception.

    :param func: The function (or method) to be executed.
    :param args: Positional arguments to pass to the function.
    :param retries: Maximum number of retries before failing.
    :param base_delay: Initial delay (in seconds).
    :param backoff_factor: Multiplicative factor by which the delay increases each retry.
    :param kwargs: Keyword arguments to pass to the function.
    :return: Whatever `func` returns if it succeeds.
    :raises: The last exception if all retries fail.
    """
    attempt = 0
    while True:
        try:
            return func(*args, **kwargs)
        except Exception as e:
            attempt += 1
            if attempt >= retries:
                print(f"[ExponentialBackoff] Exhausted retries for {func.__name__}. Error: {e}")
                raise
            sleep_time = base_delay * (backoff_factor ** (attempt - 1)) + random.uniform(0, 1)
            print(f"[ExponentialBackoff] Attempt {attempt}/{retries} for {func.__name__} failed with error: {e}. "
                  f"Sleeping for {sleep_time:.2f} seconds.")
            time.sleep(sleep_time)

def _get_iris_config():
    """
    Fetch and cache the IRIS URL + API key from Secrets Manager.

    Cached at module scope so warm Lambda invocations reuse it instead of
    calling Secrets Manager on every request.
    """
    global _iris_config_cache
    if _iris_config_cache is None:
        secrets_client = boto3.client('secretsmanager')
        response = exponential_backoff_retry(
            secrets_client.get_secret_value,
            SecretId=IRIS_SECRET_NAME,
            retries=3,
            base_delay=1,
            backoff_factor=2
        )
        _iris_config_cache = json.loads(response['SecretString'])
    return _iris_config_cache


def _post_to_iris(iris_url, api_key, data):
    request = urllib.request.Request(
        iris_url,
        data=data,
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
            # If IRIS expects a different header name/scheme
            # (e.g. "X-API-Key: <key>"), swap it in here.
        },
        method='POST'
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        if not (200 <= response.status < 300):
            raise Exception(f"IRIS returned unexpected status {response.status}")
        return response.status


def notify_iris_file_ready(bucket_name, result_key, filename):
    """
    Notify the IRIS API that a remediated PDF is ready for download.

    Non-fatal by design: a failed IRIS notification is logged but does not
    raise, so it never fails the overall remediation workflow.

    Args:
        bucket_name (str): S3 bucket containing the completed file.
        result_key (str): S3 key of the completed file, e.g.
            "result/COMPLIANT_{filename}.pdf".
        filename (str): Original filename, useful for IRIS to match the
            file back to its request.

    Returns:
        bool: True if IRIS acknowledged the notification, False otherwise.
    """
    try:
        config = _get_iris_config()
        iris_url = config['url']
        api_key = config['api_key']
    except Exception as e:
        print(f"Filename: {filename} | Could not load IRIS config from Secrets Manager: {e}")
        return False

    payload = {
        "bucket": bucket_name,
        "key": result_key,
        "filename": filename,
        "status": "ready",
    }
    data = json.dumps(payload).encode('utf-8')

    try:
        status = exponential_backoff_retry(
            _post_to_iris,
            iris_url,
            api_key,
            data,
            retries=3,
            base_delay=1,
            backoff_factor=2
        )
        print(f"Filename: {filename} | IRIS notified successfully (status {status})")
        return True
    except Exception as e:
        print(f"Filename: {filename} | IRIS notification failed after retries: {e}")
        return False

