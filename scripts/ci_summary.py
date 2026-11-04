"""Publish bounded, redacted JUnit diagnostics to the official Actions step summary."""
import os
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET


def redact(text):
    for pattern in (r'gh[pousr]_[A-Za-z0-9]{20,}', r'github_pat_[A-Za-z0-9_]{20,}',
                    r'(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}', r'\bAKIA[0-9A-Z]{16}\b',
                    r'(?i)Bearer\s+[^\s<>]+'):
        text = re.sub(pattern, '[REDACTED]', text)
    return text


def summarize(path):
    if not path.exists():
        return '## Test diagnostics\n\nJUnit report is unavailable; inspect the earlier installation/build step.\n'
    cases = list(ET.parse(path).getroot().iter('testcase'))
    failures = [case for case in cases if case.find('failure') is not None or case.find('error') is not None]
    skipped = [case for case in cases if case.find('skipped') is not None]
    lines = ['## Test diagnostics', '', f'{len(cases)-len(failures)-len(skipped)} passed, {len(failures)} failed, {len(skipped)} skipped.', '']
    for case in failures:
        issue = case.find('failure')
        if issue is None:
            issue = case.find('error')
        name = case.get('classname','') + '.' + case.get('name','')
        lines.extend(['### ' + name, '', '```text', (issue.text or issue.get('message',''))[:16000].replace('```','~~~'), '```', ''])
    if skipped:
        lines.extend(['### Skipped', ''])
        for case in skipped:
            lines.append('- ' + case.get('name','') + ': ' + case.find('skipped').get('message',''))
    return redact('\n'.join(lines)) + '\n'


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    result = summarize(Path(sys.argv[1]))
    destination = os.environ.get('GITHUB_STEP_SUMMARY')
    if destination:
        with open(destination,'a',encoding='utf-8') as stream:
            stream.write(result)
    else:
        print(result)
    if os.environ.get('GITHUB_ACTIONS') == 'true' and re.search(r'[1-9][0-9]* failed', result):
        # Official Actions annotations remain visible even when log viewing needs sign-in.
        escaped = result[:16000].replace('%','%25').replace('\r','%0D').replace('\n','%0A')
        print('::error title=Windows test diagnostics::' + escaped)
