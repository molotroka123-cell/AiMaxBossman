def agent_mode_label(mode):
    mode = str(mode).strip().lower() if mode is not None else ''
    labels = {
        'chat': 'Чат',
        'agent': 'Агент',
        'auto': 'Авто',
        'code': 'Код',
        'computer': 'Компьютер',
        'self-improve': 'Самоулучшение',
    }
    return labels.get(mode, 'Чат')
