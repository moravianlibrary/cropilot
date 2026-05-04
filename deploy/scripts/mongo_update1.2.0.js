db.titles.updateMany(
  { "scans.scan_name": { $exists: false } },
  {
    $set: { "scans.$[elem].scan_name": "" }
  },
  {
    arrayFilters: [{ "elem.scan_name": { $exists: false } }]
  }
);

db.titles.updateMany(
  { crop_model: { $exists: true } },
  [
    {
      $set: {
        "settings.crop_model": "$crop_model",
        "settings.rotation_model": "rotate-300e-best"
      }
    },
    {
      $unset: "crop_model"
    }
  ]
);

db.groups.updateMany(
  { default_model: { $exists: true } },
  [
    {
      $set: {
        "default_settings.crop_model": "$default_model",
        "default_settings.rotation_model": "rotate-300e-best"
      }
    },
    {
      $unset: "default_model"
    }
  ]
);