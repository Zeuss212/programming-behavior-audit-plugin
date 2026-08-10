const config = {
  test: {
    environment: 'node',
    include: ['src/**/*.spec.ts'],
    clearMocks: true,
    restoreMocks: true,
  },
};

export = config;
