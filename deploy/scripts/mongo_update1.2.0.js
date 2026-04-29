db.titles.updateMany(
  { scan_name: { $exists: false } },
  { $set: { scan_name: "" } }
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