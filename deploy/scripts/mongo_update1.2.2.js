db.groups.updateMany(
  {},
  {
    $set: {
    "default_settings.rotation_model": "text"
    }
  }
);