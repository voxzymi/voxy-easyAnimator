// Every word the user reads in the panel lives here, so it can be checked for length and jargon (tests/strings.test.js).
(function (root) {
  'use strict';
  const S = {
    appName: 'Voxy EasyAnimator',
    empty: {
      noComp: 'Open a composition, then select your character layer.',
      noLayer: 'Select one character layer in the timeline. Its picture will show here.',
      manyLayers: 'Select just one layer to see it here.',
      noProject: 'Open or create a project to get started.',
    },
    buttons: {
      makeLayers: 'Make Layers', addToProject: 'Add to Project', backgroundRemoval: 'Background Removal',
      upscale: 'Upscale 2x', applyInProject: 'Apply in Project', discard: 'Discard', restore: 'Restore',
      combine: 'Combine', saveProject: 'Save Project', cutHair: 'Cut hair into subdivisions', testSetup: 'Test setup',
      help: 'Help', fullScreen: 'Full screen', normalSize: 'Normal size', openLog: 'Open log file', tryAgain: 'Try again',
      useSample: 'Try the sample character', closeWelcome: 'Got it', refresh: 'Refresh picture',
      openBackup: 'Open the backup', noThanks: 'No thanks', openHelp: 'Open help',
    },
    labels: {
      group: 'Group', parts: 'Parts', hairCount: 'Sections', hairSmoothing: 'Smoothing', replaceHair: 'Replace the single Hair layer',
      organize: 'Organize groups as', precomps: 'Precomps (one per group)', folders: 'Folders (parts stay in one composition)',
      background: 'Background', backgroundFill: 'Background fill', fillAuto: 'Automatic', fillModel: 'AI fill', fillPatch: 'Flat colors',
      autosave: 'Autosave every', minutes: 'minutes', autosaveOff: 'Off', settings: 'Settings', lowMemory: 'Low memory mode',
      selectedLayer: 'Selected layer', original: 'Original', upscaled: 'Upscaled 2x', preview: 'Preview', redrawn: 'Redrawn background',
      allVisible: 'All parts are shown', someHidden: 'Some parts are hidden', allHidden: 'All parts are hidden',
      showPart: 'Show this part', hidePart: 'Hide this part', showGroup: 'Show all parts in this group', hideGroup: 'Hide all parts in this group',
      onlyPanel: 'The eye only changes this panel. Your project does not change.',
      eyeNote: 'Hidden parts are not added.',
    },
    steps: {
      title: 'Welcome to Voxy EasyAnimator',
      intro: 'Four steps turn a flat character into layers you can animate.',
      list: ['Pick a layer.', 'Press Make Layers.', 'Hide any parts you do not want with the eye buttons.', 'Press Add to Project.'],
      sample: 'Want to see it work first? Try the sample character.',
    },
    status: {
      working: 'Working. This can take a little while.', makingLayers: 'Cutting your character into parts.',
      adding: 'Adding the parts to your project.', upscaling: 'Making the picture twice as big.', hairWorking: 'Cutting the hair into sections.',
      starting: 'Starting Voxy.', savedAt: (t) => 'Saved at ' + t + '.', backupAt: (t) => 'Backup saved at ' + t + '.',
      added: (n) => n + (n === 1 ? ' part was added to your project.' : ' parts were added to your project.'),
      restored: 'The last Add to Project was removed.', applied: (name) => 'The picture of "' + name + '" is now twice as big.',
      partsReady: (n) => n + (n === 1 ? ' part is ready.' : ' parts are ready.'),
      skipped: (n) => n + (n === 1 ? ' part had nothing in it and was left out.' : ' parts had nothing in them and were left out.'),
      hairReady: (n) => 'The hair is cut into ' + n + ' sections.', hairCombined: 'The hair is one part again.',
      upscaleReady: 'The bigger picture is ready. Nothing in your project has changed yet.',
      discarded: 'The bigger picture was thrown away. Your project is unchanged.',
      cpuMode: 'Voxy is using the slow mode. Update your NVIDIA driver to use the fast mode.',
      testing: 'Checking your setup.', testOk: 'Everything is ready.', testNotOk: 'Some things need attention.',
      sampleReady: 'The sample character worked.', noChange: 'Nothing to restore.',
      recovery: 'After Effects closed unexpectedly last time. Voxy has a backup of your project.',
      hairOnlyHere: 'Pick the Hair group to cut the hair.',
    },
    help: { title: 'Help', intro: 'The full guide opens in a new window.' },
    fallbackError: {
      user_message: 'Something went wrong.', what_to_do: 'Try again. If it keeps happening, open the help page.', help_topic: 'troubleshooting',
    },
  };
  if (typeof module === 'object' && module.exports) module.exports = S; else (root.Voxy = root.Voxy || {}).strings = S;
})(typeof self !== 'undefined' ? self : this);
