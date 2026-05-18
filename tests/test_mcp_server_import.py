def test_module_imports():
    # build_server() requires AGENT_HUB_DB; just check importability.
    import agent_hub.mcp_server.server  # noqa: F401
    import agent_hub.mcp_server.__main__  # noqa: F401
