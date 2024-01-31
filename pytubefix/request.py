"""Implements a simple wrapper around urlopen."""
import http.client
import json
import logging
import re
import socket
from functools import lru_cache
from urllib import parse
from urllib.error import URLError
from urllib.request import Request, urlopen
from copy import deepcopy
import requests
from pytubefix.exceptions import RegexMatchError, MaxRetriesExceeded
from pytubefix.helpers import regex_search

logger = logging.getLogger(__name__)
default_range_size = 9437184  # 9MB

base_headers = {"accept-language": "en-US,en"}


def get(url, extra_headers=None, timeout=30):
    if extra_headers is None:
        extra_headers = {}
    headers = deepcopy(base_headers)
    headers.update(extra_headers)
    rq = requests.get(url, headers=headers, timeout=timeout)
    if rq:
        return rq.content.decode('utf-8')


def post(url, extra_headers=None, data=None, timeout=30):
    if extra_headers is None:
        extra_headers = {}
    if data is None:
        data = {}
    headers = deepcopy(base_headers)
    headers.update(extra_headers)
    rq = requests.post(url, headers=headers, json=data, timeout=timeout)
    if rq:
        return rq.content.decode('utf-8')


def seq_stream(
            url,
            timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
            max_retries=0):

    """Read the response in sequence.
    :param str url: The URL to perform the GET request for.
    :rtype: Iterable[bytes]
    """
    # YouTube expects a request sequence number as part of the parameters.
    split_url = parse.urlsplit(url)
    base_url = '%s://%s/%s?' % (split_url.scheme, split_url.netloc, split_url.path)

    querys = dict(parse.parse_qsl(split_url.query))

    # The 0th sequential request provides the file headers, which tell us
    #  information about how the file is segmented.
    querys['sq'] = 0
    url = base_url + parse.urlencode(querys)

    segment_data = b''
    for chunk in stream(url, timeout=timeout, max_retries=max_retries):
        yield chunk
        segment_data += chunk

    # We can then parse the header to find the number of segments
    stream_info = segment_data.split(b'\r\n')
    segment_count_pattern = re.compile(b'Segment-Count: (\\d+)')
    for line in stream_info:
        match = segment_count_pattern.search(line)
        if match:
            segment_count = int(match.group(1).decode('utf-8'))

    # We request these segments sequentially to build the file.
    seq_num = 1
    while seq_num <= segment_count:
        # Create sequential request URL
        querys['sq'] = seq_num
        url = base_url + parse.urlencode(querys)

        yield from stream(url, timeout=timeout, max_retries=max_retries)
        seq_num += 1
    return  # pylint: disable=R1711


# TODO: Refactor this code
def stream(url,
           timeout=socket._GLOBAL_DEFAULT_TIMEOUT,
           max_retries=0):
    r = requests.get(url, headers=base_headers, stream=True)
    for line in r.iter_content(chunk_size=1024):
        yield line


@lru_cache()
def filesize(url):
    """Fetch size in bytes of file at given URL

    :param str url: The URL to get the size of
    :returns: int: size in bytes of remote file
    """
    return int(head(url)["content-length"])


@lru_cache()
def seq_filesize(url):
    """Fetch size in bytes of file at given URL from sequential requests

    :param str url: The URL to get the size of
    :returns: int: size in bytes of remote file
    """
    total_filesize = 0
    # YouTube expects a request sequence number as part of the parameters.
    split_url = parse.urlsplit(url)
    base_url = '%s://%s/%s?' % (split_url.scheme, split_url.netloc, split_url.path)
    querys = dict(parse.parse_qsl(split_url.query))

    # The 0th sequential request provides the file headers, which tell us
    #  information about how the file is segmented.
    querys['sq'] = 0
    url = base_url + parse.urlencode(querys)
    response = requests.get(
        url, headers=base_headers,
    )

    response_value = response.read()
    # The file header must be added to the total filesize
    total_filesize += len(response_value)

    # We can then parse the header to find the number of segments
    segment_count = 0
    stream_info = response_value.split(b'\r\n')
    segment_regex = b'Segment-Count: (\\d+)'
    for line in stream_info:
        # One of the lines should contain the segment count, but we don't know
        #  which, so we need to iterate through the lines to find it
        try:
            segment_count = int(regex_search(segment_regex, line, 1))
        except RegexMatchError:
            pass

    if segment_count == 0:
        raise RegexMatchError('seq_filesize', segment_regex)

    # We make HEAD requests to the segments sequentially to find the total filesize.
    seq_num = 1
    while seq_num <= segment_count:
        # Create sequential request URL
        querys['sq'] = seq_num
        url = base_url + parse.urlencode(querys)

        total_filesize += int(head(url)['content-length'])
        seq_num += 1
    return total_filesize


def head(url):
    """Fetch headers returned http GET request.

    :param str url:
        The URL to perform the GET request for.
    :rtype: dict
    :returns:
        dictionary of lowercase headers
    """
    response = requests.head(url)
    return {k.lower(): v for k, v in response.headers.items()}
