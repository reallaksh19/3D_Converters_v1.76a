import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / 'scripts' / 'inputxml_canonical_compiler.py'
spec = importlib.util.spec_from_file_location('inputxml_canonical_compiler_pdf_test', MODULE_PATH)
compiler = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = compiler
spec.loader.exec_module(compiler)
CONTRACTS = ROOT / 'contracts' / 'inputxml' / 'v1'


def report(extra_first='', second_block=True, timestamp=True):
    date_line = 'Date: JUL 15, 2026 Time: 10:30' if timestamp else ''
    second = '''
From 20 To 30 DY= 500 mm.
Element Name= SECOND
''' if second_block else ''
    return f'''CAESAR II Input Echo
Job Name: PDF-STRICT-TEST
{date_line}
Input Listing
PIPE DATA
From 10 To 20 DX= 1000 mm.
Element Name= FIRST
Dia= 114.3 mm.
Wall= 6.02 mm.
Cor= 0 mm.
Insul Thk= 0 mm.
T1= 100 C
P1= 10 bars
PHyd= 15 bars
Mat= (106) A106 Grade B E= 200000 N./sq.mm.
Pipe Den= 7833 kg/cu.m.
Insul Den= 0 kg/cu.m.
Fluid Den= 965 kg/cu.m.
{extra_first}
{second}
NODENAMES
'''


def escape_pdf_text(value):
    return value.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')


def make_text_pdf(text):
    commands = ['BT', '/F1 8 Tf', '40 760 Td']
    first = True
    for line in text.splitlines():
        if not first:
            commands.append('0 -10 Td')
        commands.append(f'({escape_pdf_text(line)}) Tj')
        first = False
    commands.append('ET')
    stream = ('\n'.join(commands) + '\n').encode('latin-1')
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'endstream',
    ]
    data = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for index, body in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f'{index} 0 obj\n'.encode())
        data.extend(body)
        data.extend(b'\nendobj\n')
    xref = len(data)
    data.extend(f'xref\n0 {len(objects)+1}\n'.encode())
    data.extend(b'0000000000 65535 f \n')
    for offset in offsets[1:]:
        data.extend(f'{offset:010d} 00000 n \n'.encode())
    data.extend(f'trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    return bytes(data)


class PdfInputEchoAdapterTests(unittest.TestCase):
    def compile(self, source, *, extracted=True, extra_context=None):
        context = {
            'version': '11.00',
            'north': [0, 1, 0],
            'lineId': 'PDF-L-100',
        }
        if extracted:
            context.update({'sourceKind': 'extracted-text', 'extractor': 'unit-test'})
        context.update(extra_context or {})
        return compiler.compile_document(
            source if isinstance(source, bytes) else source.encode(),
            source_name='input-echo.pdf' if not extracted else 'input-echo.txt',
            source_repository='reallaksh19/3D_Converters',
            source_path='test://input-echo',
            producer='pdf-input-echo-adapter-test',
            registry_path=CONTRACTS / 'inputxml-dialect-registry.json',
            alias_registry_path=CONTRACTS / 'catalogs' / 'inputxml-alias-registry.json',
            canonical_xsd_path=CONTRACTS / 'inputxml-canonical-v1.xsd',
            categorized_xsd_path=CONTRACTS / 'categorized-inputxml-v3.xsd',
            semantic_rules_path=CONTRACTS / 'inputxml-semantic-rules-v1.sch',
            dialect_override='pdf-input-echo',
            context=context,
        )

    def test_extracted_text_passes_and_converts_bar_to_kpa(self):
        root, ledger, validation = self.compile(report())
        self.assertIsNotNone(root)
        self.assertEqual('PASS', validation['status'])
        elements = root.findall('PIPINGMODEL/PIPINGELEMENT')
        self.assertEqual(2, len(elements))
        self.assertEqual('1000', elements[0].get('PRESSURE_C1'))
        self.assertEqual('1500', elements[0].get('HYDRO_PRESSURE'))
        self.assertEqual('114.3', elements[1].get('DIAMETER'))
        self.assertEqual('6.02', elements[1].get('WALL_THICK'))
        self.assertEqual('0', elements[0].get('DELTA_Y'))
        self.assertTrue(any(row['sourceType'] == 'PDF_INPUT_ECHO_INHERITANCE' for row in ledger['records']))
        records = root.xpath('//ext:Record', namespaces={'ext': compiler.EXT_NS})
        self.assertTrue(any(row.get('name') == 'PdfExtractedInputEchoText' for row in records))

    def test_raw_text_layer_pdf_passes(self):
        root, ledger, validation = self.compile(make_text_pdf(report(second_block=False)), extracted=False)
        self.assertIsNotNone(root)
        self.assertEqual('PASS', validation['status'])
        provenance = [row for row in ledger['records'] if row['sourceEntityId'] == 'document'][0]['sourceFields']
        self.assertEqual('PDF_TEXT_LAYER', provenance['sourceKind'])
        self.assertEqual(1, provenance['pageCount'])

    def test_missing_report_timestamp_blocks_without_clock_default(self):
        root, ledger, validation = self.compile(report(timestamp=False))
        self.assertIsNone(root)
        self.assertEqual('BLOCKED', validation['status'])
        self.assertTrue(any('current-clock substitution is forbidden' in row['message'] for row in ledger['diagnostics']))

    def test_incomplete_restraint_semantics_are_preserved_and_block(self):
        root, ledger, validation = self.compile(report(extra_first='RESTRAINTS\nNode 20 +Y\n', second_block=False))
        self.assertIsNotNone(root)
        self.assertEqual('BLOCKED', validation['status'])
        self.assertEqual([], root.findall('PIPINGMODEL/PIPINGELEMENT/RESTRAINT'))
        self.assertTrue(any(row['sourceType'] == 'PDF_RESTRAINT_BLOCK' for row in ledger['records']))

    def test_valve_rigid_identity_blocks_generic_rigid_projection(self):
        root, ledger, validation = self.compile(report(extra_first='RIGID Weight= 100 lb. Type=Valve\n', second_block=False))
        self.assertIsNotNone(root)
        self.assertEqual('BLOCKED', validation['status'])
        self.assertEqual(1, len(root.findall('PIPINGMODEL/PIPINGELEMENT/RIGID')))
        rigid_rows = [row for row in ledger['records'] if row['sourceType'] == 'PDF_RIGID']
        self.assertEqual('UNSUPPORTED_BLOCKING', rigid_rows[0]['disposition'])

    def test_extracted_text_requires_explicit_provenance(self):
        root, ledger, validation = self.compile(report(), extracted=False)
        self.assertIsNone(root)
        self.assertEqual('BLOCKED', validation['status'])


if __name__ == '__main__':
    unittest.main()
