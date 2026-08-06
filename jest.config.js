const createJupyterLabJestConfig = require('@jupyterlab/testutils/lib/jest-config');
const { testRegex, ...jupyterLabJestConfig } =
  createJupyterLabJestConfig(__dirname);

module.exports = {
  ...jupyterLabJestConfig,
  collectCoverageFrom: ['src/**/*.ts', '!src/**/*.d.ts'],
  coverageThreshold: {
    'src/notebookMonitor.ts': {
      statements: 70,
      branches: 60,
      functions: 70,
      lines: 70
    },
    'src/pythonFileMonitor.ts': {
      statements: 70,
      branches: 60,
      functions: 70,
      lines: 70
    }
  },
  testMatch: ['<rootDir>/src/**/__tests__/**/*.spec.ts'],
  modulePathIgnorePatterns: [
    ...(jupyterLabJestConfig.modulePathIgnorePatterns ?? []),
    '<rootDir>/myextension/labextension/package\\.json$',
    '<rootDir>/.venv/share/jupyter/lab/static/package\\.json$',
    '<rootDir>/.venv/lib/python3\\.12/site-packages/jupyterlab/staging/package\\.json$',
    '<rootDir>/.superpowers/',
    '<rootDir>/.venv/share/jupyter/labextensions/myextension/package\\.json$'
  ]
};
