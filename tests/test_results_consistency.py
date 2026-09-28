"""README, docs/experiment_results.md and the deployed model must all show the numbers in docs/experiment_results.json."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
RESULTS = ROOT / 'docs' / 'experiment_results.json'


@unittest.skipUnless(RESULTS.is_file(), 'run run_experiments.py first')
class ResultsConsistencyTests(unittest.TestCase):
    def setUp(self):
        from run_experiments import README_END, README_START, readme_section, report_markdown
        self.results = json.loads(RESULTS.read_text(encoding='utf-8'))
        self.readme_section, self.report_markdown = readme_section, report_markdown
        self.start, self.end = README_START, README_END

    def test_readme_block_is_generated_from_results(self):
        readme = (ROOT / 'README.md').read_text(encoding='utf-8')
        block = readme.split(self.start, 1)[1].split(self.end, 1)[0].strip()
        self.assertEqual(block, self.readme_section(self.results).strip(), 'README 的實測成效被手動改過；請重新執行 run_experiments.py')

    def test_markdown_report_is_generated_from_results(self):
        report = (ROOT / 'docs' / 'experiment_results.md').read_text(encoding='utf-8')
        self.assertEqual(report, self.report_markdown(self.results))

    def test_website_metrics_come_from_the_same_run(self):
        final = next(m for m in self.results['methods'] if m['name'] == 'lr_full_ft')
        deployed = json.loads((ROOT / 'models' / 'fusion_model_social.json').read_text(encoding='utf-8'))['trained_on']
        self.assertEqual(deployed['test_metrics'], final['official_test'])
        self.assertEqual(deployed['conformal_test'], final['conformal_test'])
        self.assertAlmostEqual(deployed['auc'], final['auc'])


if __name__ == '__main__':
    unittest.main()
