"""Human-readable failures shared by task and conversation views."""


def failure_summary(error, result):
    answer = str(result or "").strip()
    message = str(error or "")
    if "no_verified_action" in message or "effectful tool did not succeed" in message:
        notice = ("Действие не подтверждено. Проверьте инструменты и разрешения выбранного "
                  "агента или выберите локального исполнителя с нужным инструментом.")
        return f"{answer}\n\n{notice}" if answer else notice
    if answer:
        return answer
    return f"Задача завершилась с ошибкой: {message}" if message else None
