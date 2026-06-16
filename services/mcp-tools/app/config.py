from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "MCP Tool Server"

    # Which toolset this instance serves. One image, deployed multiple times with
    # a different TOOLSET so we can demonstrate the registry/gateway routing a
    # call to the correct *server* as well as the correct *tool*.
    #   utility   -> calculator, current_datetime, unit_convert, random_number, text_stats
    #   knowledge -> web_search, wikipedia
    #   all       -> every tool (handy for local dev)
    toolset: str = "all"

    class Config:
        env_file = ".env"


settings = Settings()
