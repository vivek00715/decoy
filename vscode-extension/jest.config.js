/** @type {import('ts-jest').JestConfigWithTsJest} */
module.exports = {
  preset: "ts-jest",
  testEnvironment: "node",
  roots: ["<rootDir>/src"],
  testMatch: ["**/*.test.ts"],
  // extension.ts (the only module that imports the real "vscode" API) is
  // deliberately NOT unit tested here -- see PHASE8_TESTING_SCOPE.md for
  // why a mock of vscode's API would overstate confidence rather than
  // earn it. Everything under test either has no vscode dependency or is
  // reached through PanelController's injectable PanelHost interface.
  testPathIgnorePatterns: ["/node_modules/", "extension.test.ts"],
};
