import pytest
from bossmobile.modes import agent_mode_label

def test_agent_mode_label():
    assert agent_mode_label('CHAT') == 'Чат'
    assert agent_mode_label('AGENT') == 'Агент'
    assert agent_mode_label('AUTO') == 'Авто'
    assert agent_mode_label('CODE') == 'Код'
    assert agent_mode_label('COMPUTER') == 'Компьютер'
    assert agent_mode_label('SELF-IMPROVE') == 'Самоулучшение'
    
    assert agent_mode_label(' chat ') == 'Чат'
    assert agent_mode_label(' agent ') == 'Агент'
    
    assert agent_mode_label(None) == 'Чат'
    assert agent_mode_label('') == 'Чат'
    assert agent_mode_label('unknown') == 'Чат'
