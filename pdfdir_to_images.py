import os
from pdf2image import convert_from_path

# Directory containing PDFs
pdf_directory = '/Users/jvl/Downloads/0000'

# Output directory for images
output_directory = '/Users/jvl/Downloads/0000/images'
os.makedirs(output_directory, exist_ok=True)

# Iterate through all PDFs in the directory
for pdf_filename in os.listdir(pdf_directory):
  if pdf_filename.endswith('.pdf'):
    pdf_path = os.path.join(pdf_directory, pdf_filename)

    # Convert PDF to list of images
    images = convert_from_path(pdf_path)

    # Save each image with a filename indicating the page number
    pdf_basename = os.path.splitext(pdf_filename)[0]
    for i, image in enumerate(images):
      image_filename = f"{pdf_basename}_page_{i+1}.png"
      image_path = os.path.join(output_directory, image_filename)
      image.save(image_path, 'PNG')

print("PDF conversion completed.")
