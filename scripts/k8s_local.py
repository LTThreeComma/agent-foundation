"""Refuse the retired local deployment flow before touching cluster state."""


def main() -> None:
    raise SystemExit("New Service Kubernetes setup is not implemented; existing cluster stores were not touched")


if __name__ == "__main__":
    main()
