from importlib.metadata import version, PackageNotFoundError

def main():
    try:
        v = version("remoterf")
    except PackageNotFoundError:
        v = "unknown"
    print(v)