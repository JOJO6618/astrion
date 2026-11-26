ALTER TABLE messages ADD COLUMN has_images INTEGER GENERATED ALWAYS AS (
    CASE json_type(body,'$.images')
        WHEN 'array' THEN json_array_length(body,'$.images') > 0
        WHEN 'object' THEN json_extract(body,'$.images') <> '{}'
        WHEN 'text' THEN length(json_extract(body,'$.images')) > 0
        ELSE coalesce(json_extract(body,'$.images'),0) <> 0 END OR
    CASE json_type(body,'$.metadata.images')
        WHEN 'array' THEN json_array_length(body,'$.metadata.images') > 0
        WHEN 'object' THEN json_extract(body,'$.metadata.images') <> '{}'
        WHEN 'text' THEN length(json_extract(body,'$.metadata.images')) > 0
        ELSE coalesce(json_extract(body,'$.metadata.images'),0) <> 0 END
) VIRTUAL;
CREATE INDEX messages_with_images ON messages(conversation) WHERE has_images;

ALTER TABLE messages ADD COLUMN has_videos INTEGER GENERATED ALWAYS AS (
    CASE json_type(body,'$.videos')
        WHEN 'array' THEN json_array_length(body,'$.videos') > 0
        WHEN 'object' THEN json_extract(body,'$.videos') <> '{}'
        WHEN 'text' THEN length(json_extract(body,'$.videos')) > 0
        ELSE coalesce(json_extract(body,'$.videos'),0) <> 0 END OR
    CASE json_type(body,'$.metadata.videos')
        WHEN 'array' THEN json_array_length(body,'$.metadata.videos') > 0
        WHEN 'object' THEN json_extract(body,'$.metadata.videos') <> '{}'
        WHEN 'text' THEN length(json_extract(body,'$.metadata.videos')) > 0
        ELSE coalesce(json_extract(body,'$.metadata.videos'),0) <> 0 END
) VIRTUAL;
CREATE INDEX messages_with_videos ON messages(conversation) WHERE has_videos;
PRAGMA user_version = 2;
